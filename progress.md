# 进度日志

## 2026-09-05 - Task: PHASE1_AI_DESIGN_APPROVAL_BOUNDARY_V1_STAGE_B

### What was done

完成 AI Design Approval Boundary V1：新增绑定当前 AI Design 身份/哈希的不可变人工审批回执、精确一次与 fail-closed 校验；Candidate Proposal 的服务、CLI 和 Web 生成入口统一经过审批门禁；拒绝、过期、Objective 不匹配和回执完整性失败均保持阻断。完成 Objective Reconciliation 的审批前后状态投影、Canonical Authority 说明、loopback Web API、AI Design Console 人工批准/拒绝与显式 Candidate Proposal 生成界面，并补齐专项回归测试与中文文档。

### Testing

- 目标仓库显式使用 `E:\llmwiki\chanlun-trading-system-github-v2\src` 作为源码路径执行 `python -m compileall -q src`：通过。
- `python -m pytest --collect-only -q`：596 tests collected；1 个既有 legacy autonomous pilot skip。
- Approval、Candidate Generation、Reconciliation、AI Design、Webapp 相关专项：51 passed，3 warnings。
- 完整 `tests/research_factory`：276 passed、33 个因 sanitized 历史数据/合同文件缺失而失败、1 skipped；未出现新的 `SOURCE_FAILURE`、`ImportError`、`ModuleNotFoundError` 或 `SyntaxError`。
- 完整 `tests/research_console`：24 passed、4 个因 sanitized 历史数据/Canonical artifact 缺失而失败；`tests/test_webapp.py`：8 passed。
- 前端已执行 `npm ci`、`npm test`（8 passed）和 `npm run build`（通过）；构建仅有既有 chunk size warning，`frontend/dist` 未纳入版本控制。
- 真实工作区 Objective `RESEARCH_OBJECTIVE_EVOLUTION_V1_BE80F967D29706F19703C869` 只读验收通过：`AI_DESIGN_AWAITING_CONFIRMATION`、`HUMAN_CONFIRM_AI_RESEARCH_DESIGN`、`required_action_supported=true`、`safe_to_advance=false`，Candidate/Trial 为 0，预算保持 `used=0/reserved=0/remaining=4`，相关文件前后哈希一致。

### Notes

改动文件清单：

- `docs/AI_Design_Approval_Boundary_V1.md`：新增 Stage B 审批边界、回执、门禁和 Web 说明。
- `docs/AI_Research_Design_for_Evolution_Objective_V1.md`：补充审批状态链和回执绑定说明。
- `docs/Candidate_Generation_Governance_V1.md`：将人工审批回执设为 Candidate Proposal 生成前置门禁。
- `docs/Canonical_Authority_and_Objective_Reconciliation_V1.md`：明确 AI Design Approval 的 canonical authority 与审批前后对账状态。
- `frontend/src/console/ResearchConsole.vue`：接入审批动作后的视图刷新。
- `frontend/src/console/api.ts`：新增审批确认与显式 Candidate Proposal 生成 API。
- `frontend/src/console/components/ResearchEvolutionAIDesign.vue`：展示审批证据并提供人工批准/拒绝和显式生成入口。
- `frontend/src/console/types.ts`：补充 AI Design Approval 读模型字段。
- `src/chanlun_trader/research_console.py`：将审批回执接入 AI Design Console 只读模型。
- `src/chanlun_trader/research_factory/__init__.py`：导出审批合同和状态常量。
- `src/chanlun_trader/research_factory/ai_design_approval.py`：新增 durable Approval Receipt、完整性验证、精确一次和 Candidate Gate 服务。
- `src/chanlun_trader/research_factory/candidate_generation.py`：在全部 Candidate Proposal 生成路径接入统一审批门禁并记录审批来源。
- `src/chanlun_trader/research_factory/canonical_authority.py`：登记 AI Design Approval canonical authority。
- `src/chanlun_trader/research_factory/objective_reconciliation.py`：只读取 canonical 审批回执并投影审批状态。
- `src/chanlun_trader/research_factory/research_evolution_ai_design.py`：公开稳定的 AI Design identity/hash 计算。
- `src/chanlun_trader/webapp.py`：新增审批读取、loopback 写入和显式 Candidate Proposal 生成路由。
- `tests/research_console/test_research_console_read_boundary_v1.py`：覆盖新增审批与生成路由边界。
- `tests/research_factory/test_ai_design_approval_v1.py`：新增审批、过期、拒绝、幂等、重启、Web 和零副作用专项测试。
- `tests/research_factory/test_candidate_generation_governance_v1.py`：为既有 Candidate fixture 写入显式审批并校正门禁断言。
- `tests/research_factory/test_objective_reconciliation_v1.py`：更新审批前对账动作支持断言。
- `tests/research_factory/test_research_evolution_ai_design_v1.py`：更新生成后 Console 状态断言。
- `progress.md`：追加本轮实现、验证和回滚记录。

回滚点：本轮交付为单一 Stage B 提交；如当前 HEAD 仍为本轮提交，执行 `git revert --no-edit HEAD`，如已有后续提交则用 `git log --grep="feat(research): enforce AI design approval boundary"` 定位本轮提交后执行 `git revert --no-edit <commit>`。

## 2026-09-05 - Task: CANDIDATE_EXECUTABLE_MATERIALIZATION_BRIDGE_V1

### What was done

完成 Candidate Governance Freeze 到 Executable Frozen Candidate 的断链修复：治理冻结现在停在 `CANDIDATE_GOVERNANCE_FROZEN`，新增独立的 Executable Materialization Bridge，以冻结事实构建不可变 Preview，经过第二次人工确认后复用既有 `DurableFrozenCandidateContractV1` 写入 canonical durable store，并在 `from_dict()` 与 `provider_candidate_payload()` 均通过后进入 `READY_FOR_STRUCTURAL_PREFLIGHT`。同步扩展 Objective Reconciliation 双层状态、Canonical Authority、Console/Web loopback 读写边界与 CLI 入口；没有自动运行 Structural、Predictive、Trial、Backtest、AI 或预算操作。

### Testing

- 目标仓库执行 `python -m compileall -q src`：通过。
- `python -m pytest --collect-only -q`：606 tests collected；1 个既有 legacy autonomous pilot skip。
- Bridge、Candidate Governance、Objective Reconciliation 专项：35 passed，3 warnings；覆盖缺字段、无默认补齐、语义漂移、Preview 不可变、hash/identity 冲突、provider gate、精确一次、重启恢复、Web/CLI 和 daemon durable-only 读取。
- AI Design Approval 专项：9 passed；Durable Contract 专项的 5 个历史 fixture 用例因 sanitized 仓库缺少 `data/research/strategy_candidate_registry/registry_v2.json` 未能运行。
- Structural/Daemon 专项：23 passed、4 个因 sanitized 仓库缺少既有 Objective/Candidate runtime 工件而失败；与基线 `a1731af8ba0ffcc03cf09a6e067bb2e423084f1d` 同一组测试结果一致。
- `tests/research_factory`：286 passed、33 failed、1 skipped；失败集合与基线 clean worktree 完全一致，归类为 `KNOWN_EXTERNAL_DATA_DRIFT`，未出现新的 `SOURCE_FAILURE`、`ImportError`、`ModuleNotFoundError` 或 `SyntaxError`。
- `tests/research_console`：24 passed、4 个既有 sanitized 数据/集成失败；基线 clean worktree 同样为 24 passed、4 failed。`tests/test_webapp.py`：8 passed。
- 前端执行 `npm ci`、`npm test`（8 passed）和 `npm run build`（通过）；仅有既有 chunk size warning，`frontend/dist` 与 `node_modules` 未纳入交付。
- 真实 Workspace 只读验收：新对账逻辑返回 `AI_DESIGN_AWAITING_CONFIRMATION`、`HUMAN_CONFIRM_AI_RESEARCH_DESIGN`、`safe_to_advance=false`、`structural_preflight_ready=false`，Candidate/Durable/Trial 均为 0，预算为 `used=0/reserved=0/remaining=4`，关键 5 个 Objective/AI/lineage 工件在对账前后 hash 一致。

### Notes

改动文件清单：

- `src/chanlun_trader/research_factory/candidate_executable_materialization.py`：新增两层冻结之间的 Preview、人工确认、Durable Contract 校验、精确一次和恢复服务。
- `src/chanlun_trader/research_factory/candidate_generation.py`：将治理 Freeze 停在 `CANDIDATE_GOVERNANCE_FROZEN`，新增执行冻结状态和下一动作。
- `src/chanlun_trader/research_factory/objective_reconciliation.py`：识别 Preview、Provider 无效、Canonical 冲突和 Structural readiness。
- `src/chanlun_trader/research_factory/canonical_authority.py`：明确 Proposal、治理回执、轻量 Registry、Durable Contract 与 runtime projection 的 authority。
- `src/chanlun_trader/research_factory/__init__.py`：导出 Bridge 服务、状态和动作常量。
- `src/chanlun_trader/research_console.py`：把执行合同物化状态接入只读 Candidate Console view。
- `src/chanlun_trader/webapp.py`：新增 loopback-only Preview/Confirm 路由，不直接触发下游执行。
- `frontend/src/console/api.ts`：新增物化状态、Preview 和 Confirm API。
- `frontend/src/console/types.ts`：新增 Candidate materialization view/result 类型。
- `frontend/src/console/components/CandidateProposalGovernance.vue`：展示双层冻结并提供显式 Preview/Confirm 操作。
- `tests/research_factory/test_candidate_executable_materialization_v1.py`：新增 Bridge synthetic acceptance coverage。
- `tests/research_factory/test_candidate_generation_governance_v1.py`：更新治理 Freeze 后状态断言。
- `tests/research_factory/test_objective_reconciliation_v1.py`：更新治理冻结后的可推进边界断言。
- `tests/research_console/test_research_console_read_boundary_v1.py`：更新新增 Console 路由边界断言。
- `docs/Candidate_Executable_Materialization_Bridge_V1.md`：记录状态机、authority、Preview/Confirm、恢复与禁止边界。
- `progress.md`：追加本轮实现、验证和回滚记录。

回滚点：本轮为单一 feature branch 提交；推送后如需回滚，执行 `git revert --no-edit <本轮 commit hash>`，保留后续提交历史，不回写 `main`。

## 2026-09-06 - Task: STRUCTURAL_ENTRY_AND_PROJECTION_RECONCILIATION_V1

### What was done

完成 Structural Entry & Projection Reconciliation V1：将 `READY_FOR_STRUCTURAL_PREFLIGHT` 固定为资格边界，新增 CLI、Web、Daemon 和 domain service 共用的显式 `RUN_STRUCTURAL_PREFLIGHT` Entry Gate；仅唯一且完整通过 `provider_candidate_payload()` 的 `DurableFrozenCandidateContractV1` 可进入既有 canonical Structural Provider。Structural Provider evidence 与 canonical reconciled result 已分层保存，结果绑定 Objective、Candidate、Durable Contract、Provider payload、data/manifest、policy、Structural contract 和 result identity，并提供 PASS、UNKNOWN/insufficient、Engineering failure 的 fail-closed 状态语义。Structural PASS 只投影到 `PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED`，不授权、不 reserve Budget、不创建 Trial、不访问 Performance。

同步收敛 Objective Reconciliation、Daemon、Orchestrator、Console 和 Web API：canonical Structural Result 是研究事实，Daemon/Orchestrator 仅为 runtime projection；restart/recover 只读取 reconciliation snapshot，不重复 Provider；projection repair 只允许 canonical → projection，Canonical Conflict 时禁止修复，并通过可审计 receipt 实现 exact-once。未修改 Candidate frozen contract、AI Design、历史 Structural/Trial/Budget/Performance artifact，也未实现 SafeRuntimeContext、Capability Registry 或 Autonomous Alpha Loop。

### Testing

- 目标仓库执行 `python -m compileall -q src`：通过。
- `python -m pytest --collect-only -q`：621 tests collected；1 个既有 legacy autonomous pilot skip。
- Structural Entry/Projection、Structural Reconciliation、Objective Reconciliation：核心组合 `33 passed`；新增 Structural Entry/Projection 与 Web boundary 组合 `23 passed`；无本轮结构断言失败。
- Daemon/Orchestrator 相关组合：`57 passed`、5 个因 target checkout 缺少历史 Candidate/partition/脱敏 Contract 而失败；归类 `KNOWN_EXTERNAL_DATA_DRIFT` / `LOCAL_WORKSPACE_INTEGRATION_ONLY`。
- Predictive authorization/trial 边界：`16 passed`、22 个因缺少既有 durable contract runtime fixture 而失败；没有新增自动授权、Trial duplication、Budget mutation 或 Performance access 失败。
- Durability/Candidate materialization 组合：`10 passed`、5 个因缺少既有 strategy candidate registry fixture 而失败；预算/Trial/Orchestrator control-plane 组合 `28 passed`、1 个因缺少既有 validation policy fixture 而失败。
- `tests/test_webapp.py`：`8 passed`；最终完整回归：`535 passed, 86 failed, 1 skipped, 3 warnings`。86 个失败均为既有真实/脱敏数据、运行 artifact、partition 或集成 fixture 缺失/漂移；未出现新的 `SOURCE_FAILURE`、`ImportError`、`ModuleNotFoundError` 或 `SyntaxError`。
- `git diff --check`：通过。
- 真实 Research Workspace 仅读验收：对账前后 1,186 个相关 artifact 哈希完全一致；Evolution Objective `RESEARCH_OBJECTIVE_EVOLUTION_V1_BE80F967D29706F19703C869` 仍为 `AI_DESIGN_AWAITING_CONFIRMATION`，Candidate=0、Durable=0、Trial=0、Budget=`0/4`；两个历史 terminal Objective 只读识别为 projection drift/已有 canonical conflict，未写入修复。

### Notes

改动文件清单：

- `src/chanlun_trader/presentation.py`：补充 Structural readiness、blocked、projection conflict 和人工授权动作的展示语义。
- `src/chanlun_trader/research_console.py`：让 Console list/status/dashboard/pipeline/Structural read model 消费 Objective Reconciliation 与 canonical Structural Result。
- `src/chanlun_trader/research_daemon.py`：移除 READY 自动执行 Structural/Predictive 的路径，增加显式 Structural Entry、projection-only recovery 和 CLI 命令。
- `src/chanlun_trader/research_factory/__init__.py`：以惰性导出方式公开 Structural Entry 与 Projection Reconciliation API，避免循环导入。
- `src/chanlun_trader/research_factory/autonomous_orchestrator_v2.py`：将真实 Objective 的 Structural 生命周期读取收敛为 canonical projection，不重写既有 Orchestrator。
- `src/chanlun_trader/research_factory/canonical_authority.py`：明确 Structural evidence、canonical Structural result、Predictive authorization 与 runtime projection 的 authority 分层。
- `src/chanlun_trader/research_factory/objective_reconciliation.py`：识别 Structural running/result/block/conflict，并从 canonical facts 计算有效状态与 projection drift。
- `src/chanlun_trader/research_factory/projection_reconciliation.py`：新增单向、可审计、exact-once 的 Daemon/Orchestrator projection repair service。
- `src/chanlun_trader/research_factory/structural_entry.py`：新增统一 Durable Contract gate、显式 Structural start、provider execution、identity 和 exact-once service。
- `src/chanlun_trader/research_factory/structural_reconciliation.py`：复用既有 Structural Provider，新增 outcome-blind canonical result 持久化和 identity-bound result reconciliation。
- `src/chanlun_trader/webapp.py`：新增 Structural readiness/reconciliation/start/repair loopback API，并让兼容入口同样强制显式动作。
- `tests/research/test_predictive_authorization.py`：校正 Structural PASS 后必须停在人工 Predictive authorization 边界的测试。
- `tests/research/test_research_daemon.py`：覆盖 READY 不自动运行、显式 Structural start、预算不变与重启恢复边界。
- `tests/research_console/test_research_console_read_boundary_v1.py`：覆盖 canonical Structural Result 优先于 stale projection 的 Console 读边界。
- `tests/research_factory/test_structural_entry_projection_reconciliation_v1.py`：新增 T01-T26 对应的 Entry、Result、exact-once、projection repair、restart、CLI/Web 和 fail-closed synthetic matrix。
- `docs/STRUCTURAL_ENTRY_AND_PROJECTION_RECONCILIATION_V1.md`：记录 authority、Entry Gate、状态机、投影、修复和禁止的 Predictive 边界。
- `progress.md`：追加本轮实现、验证、外部数据漂移分类和回滚记录。

回滚点：本轮 feature commit 生成后执行 `git revert --no-edit <本轮 commit hash>`；施工基线为 `116b91a96074cc40b5776080c0c0fe2858a1b836`，不回写 `main`。

## 2026-09-06 - Task: SAFE_RUNTIME_CONTEXT_V1

### What was done

新增统一的 `SafeRuntimeContextV1` / `SafeRuntimeContextBuilderV1` 只读领域服务。Context 先读取 `ObjectiveReconciliationServiceV1`，从 canonical/effective state 生成结果盲化、稳定哈希、带 freshness 的 Objective、Candidate、Trial、Budget、Structural、Data、Factor、Event、失败分类、机制排除和 runtime projection read model；Canonical Conflict 与预算权威歧义均 fail-closed。AI Design、Manual Handoff、Candidate Proposal 和可执行物化入口已绑定相同的 `source_context_id` / `source_context_hash`，过期上下文不能继续使用；新增只读 Console API、CLI、派生工件说明和合成验证矩阵。未调用 AI、未创建 Candidate/Trial、未执行 Structural/Predictive、未消耗预算、未修改 frozen parameter 或真实 Research Workspace。

### Testing

- `python -m compileall -q src`：通过。
- `python -m pytest --collect-only -q`：634 项收集，1 个既有 legacy skip。
- Safe Runtime Context、AI Design Approval、AI Design、Candidate Generation、Executable Materialization：`45 passed`。
- Objective Reconciliation、Structural Entry/Projection、Trial Reconciliation：`31 passed`；`tests/test_webapp.py`：`8 passed`。
- 完整回归：`548 passed, 86 failed, 1 skipped, 3 warnings`；失败归类为现有历史/脱敏 fixture 缺失、真实数据/分区漂移和既有结构入口断言，不含本轮新增 Safe Runtime Context 测试失败、导入错误、语法错误或性能字段泄漏。
- 真实 Research Workspace 仅读审计：相关 255 个 canonical/registry 工件构建前后哈希一致；`PERFORMANCE_BLIND_AUDIT=PASS`；真实 Evolution Objective 仍为 `AI_DESIGN_AWAITING_CONFIRMATION`，Candidate/Durable/Trial 为 0，Budget 为 `0/4`。
- `git diff --check`：通过。

### Notes

- `src/chanlun_trader/research_factory/safe_runtime_context.py`：新增统一安全上下文、能力摘要、身份哈希、新鲜度校验、CLI 和派生只读工件出口。
- `src/chanlun_trader/research_factory/research_evolution_ai_design.py`：AI Design 输入改由 Safe Runtime Context 构建并绑定上下文身份。
- `src/chanlun_trader/research_factory/candidate_generation.py`：Candidate Proposal 输入改由 Safe Runtime Context 提供并保存预算/上下文绑定。
- `src/chanlun_trader/research_factory/candidate_executable_materialization.py`：物化前增加 stale Safe Runtime Context gate。
- `src/chanlun_trader/research_factory/ai_design_approval.py`：审批回执及结果绑定 `source_context_id` / `source_context_hash`。
- `src/chanlun_trader/research_factory/autonomous_orchestrator_v2.py`：Manual Handoff 通过 Safe Runtime Context 生成安全目标、预算、能力与来源身份。
- `src/chanlun_trader/research_factory/manual_handoff.py`：将安全上下文身份传播到 staging、output contract 和 ready metadata。
- `src/chanlun_trader/research_console.py`：新增只读 Safe Runtime Context read model。
- `src/chanlun_trader/webapp.py`：新增 `GET /api/research-console/{objective_id}/safe-runtime-context` 只读接口。
- `src/chanlun_trader/research_factory/__init__.py`：公开 Safe Runtime Context API 和状态常量。
- `docs/安全运行时上下文_V1.md`：记录统一入口、权威边界、新鲜度、CLI/API 和真实目标基线。
- `docs/AI_Design_Approval_Boundary_V1.md`：补充审批回执的上下文身份绑定说明。
- `tests/research_factory/test_safe_runtime_context_v1.py`：覆盖只读、盲化、冲突、投影漂移、确定性、stale、CLI、Console 与 Candidate gate。
- `progress.md`：追加本轮 SAFE_RUNTIME_CONTEXT_V1 实现与验证记录。
- 回滚方式：本轮 commit 生成后，在该分支执行 `git revert --no-edit <本轮 commit hash>`；不回写 `main`，不触碰真实 Research Workspace。

## 2026-09-06 - Task: SAFE_RUNTIME_CONTEXT_V1 - clean checkout verification

### What was done

从本轮 feature commit 建立独立 clean worktree，按交付硬门禁验证提交内容可独立运行；未修改 clean worktree 内文件。

### Testing

- clean worktree `git status --short`：干净；HEAD 为本轮 Safe Runtime Context feature commit。
- clean worktree `python -m compileall -q src`：通过。
- clean worktree `python -m pytest --collect-only -q`：634 项收集，1 个既有 legacy skip。
- clean worktree Safe Runtime Context：`13 passed`；AI Design integration：`9 passed`；Candidate context hash gate：`2 passed`。

### Notes

- `progress.md`：追加 clean checkout 验证证据。
- 回滚方式：在最终分支执行 `git revert --no-edit HEAD`，即可回滚本轮 feature commit；不回写 `main`。

## 2026-09-06 - Task: PHASE1_CERTIFICATION_BLOCKER_FIXES_V1

### What was done

在指定基线 `920292f7935085119ae07bc6916b97dd7f2fb642` 上完成 Phase 1 治理认证阻断修复：Executable Materialization 改为 immutable Preview、Receipt-first confirmation、Durable Contract、projection/state 的 crash-safe journal；Objective Reconciliation 与 Structural Entry 均要求 Receipt + Contract 的完整 identity/hash match；Candidate Generation 与 Manual Handoff 在消费边界重新构建对应 SafeRuntimeContext 并 fail closed 处理 stale context；统一 `READY_FOR_STRUCTURAL_PREFLIGHT` 的下一动作；新增 Phase 1 deterministic GitHub Actions workflow 和认证文档。未启动 Predictive/Final/Prospective/Real Order，未修改真实 Research Workspace。

### Testing

- Workspace Guard 在任何源码操作前执行：repo root=`E:\llmwiki\chanlun-trading-system-github-v2`，branch=`codex/phase1-certification-blocker-fixes-v1`，base HEAD=`920292f7935085119ae07bc6916b97dd7f2fb642`，remote=`https://github.com/a135199516/trade.git`，初始工作区干净。
- `python -m compileall -q src`：通过。
- `python -m pytest --collect-only -q`：647 项收集，1 个既有 legacy autonomous pilot skip。
- Phase 1 deterministic certification suite（与 `.github/workflows/phase1-certification.yml` 一致）：`232 passed, 12 deselected, 3 warnings`。
- Predictive authorization boundary regression：`9 passed, 3 warnings`；Structural PASS 仍只停在 `PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED`。
- Crash Matrix：Receipt-only recovery、Contract-only fail closed、Contract-after-append restart、Receipt/Contract tamper/mismatch、same retry exact-once 和不同 idempotency conflict 均通过；provider call、Trial、Budget 和 Performance side effects 均为 0。
- `git diff --check`：通过。
- 真实 Research Workspace 仅读验收：对目标 Objective 的 5 个 canonical/governance/runtime 文件做 before/after SHA-256 inventory，文件数均为 5，`hashes_equal=true`；对账结果为 `AI_DESIGN_AWAITING_CONFIRMATION`、`HUMAN_CONFIRM_AI_RESEARCH_DESIGN`、`safe_to_advance=false`、Candidate=0、Durable Contract=0、Trial=0、Budget=`used=0/reserved=0/remaining=4`、Structural 未启动。
- 已知基线验证：选定的非 Phase 1 integration 测试因仓库未携带历史 registry/contract/Objective/partition fixture 而失败，详见最终报告；未将其计入 deterministic certification PASS。

### Notes

- `.github/workflows/phase1-certification.yml`：新增 Pull Request 与 `codex/**` push 的 Phase 1 deterministic certification workflow。
- `docs/PHASE1_CERTIFICATION_BLOCKER_FIXES_V1.md`：记录双层冻结、Receipt authority、crash recovery、exact-once、freshness 和边界语义。
- `src/chanlun_trader/presentation.py`：补充 materialization recovery/confirmation/structural action 展示语义。
- `src/chanlun_trader/research_console.py`：接入 materialization 状态、统一 action 和 fail-closed read model。
- `src/chanlun_trader/research_factory/__init__.py`：导出本轮状态、动作和 handoff compatibility API。
- `src/chanlun_trader/research_factory/ai_design_approval.py`：收紧 AI Design approval context identity 校验。
- `src/chanlun_trader/research_factory/autonomous_orchestrator_v2.py`：新增 governed Manual Handoff live-context gate 与显式 legacy compatibility gate。
- `src/chanlun_trader/research_factory/candidate_executable_materialization.py`：实现 Receipt-first journal、完整绑定、状态矩阵、crash injection、recovery 和 exact-once。
- `src/chanlun_trader/research_factory/candidate_generation.py`：在 Candidate Proposal consumer gate 重新构建 `AI_DESIGN` SafeRuntimeContext，并统一 Structural action。
- `src/chanlun_trader/research_factory/canonical_authority.py`：明确 Durable Contract alone 不是 Executable Authority，Receipt 是人工执行物化批准 authority。
- `src/chanlun_trader/research_factory/objective_reconciliation.py`：要求 matching Receipt + Contract 后才输出 Structural Ready，并公开物化状态字段。
- `src/chanlun_trader/research_factory/safe_runtime_context.py`：隔离 AI_DESIGN 下游工件，保持第一 canonical read source 且支持 freshness gate。
- `src/chanlun_trader/research_factory/structural_entry.py`：增加独立 Receipt + Contract defense-in-depth gate，无 Registry/Contract-only fallback。
- `tests/research_factory/test_autonomous_orchestrator_v2.py`：新增 governed stale Manual Handoff 与 legacy version gate 覆盖。
- `tests/research_factory/test_candidate_executable_materialization_v1.py`：新增 Crash Matrix、tamper、mismatch、exact-once、idempotency、Structural side-effect coverage。
- `tests/research_factory/test_candidate_generation_governance_v1.py`：新增 Data/Factor/Event/Budget/Structural/Objective stale context 矩阵。
- `tests/research_factory/test_objective_reconciliation_v1.py`：更新 Contract-only 状态与 action 断言。
- `tests/research_factory/test_safe_runtime_context_v1.py`：同步 canonical budget 与 context 状态断言。
- `progress.md`：按 append-only 规则记录本轮实施与验证证据。
- 回滚方式：提交后在 feature branch 执行 `git revert --no-edit <本轮最终 commit SHA>`；在分支 tip 直接回滚可执行 `git revert --no-edit HEAD`，不使用 force push，不回写 `main`，不触碰真实 Research Workspace。

## 2026-09-06 - Task: PHASE1_CERTIFICATION_FINAL_CLOSURE_V1

### What was done

在指定基线 `20ab4fdcf04b61b002b0a446757dac2ed8c33707` 上创建 `codex/phase1-certification-final-closure-v1`，完成 Phase 1 最终认证阻断收口：修复 Python 3.11 下的 trial budget identity 语法错误；在写入 Confirmation Receipt 前对已有 Durable Contract 与 immutable Preview 的 `content_hash` 做 fail-closed 对账；将 Confirmation Receipt 明确为人工批准证据而非 READY 状态工件；同步 Objective Reconciliation、Canonical Authority、工作流 whitespace gate、测试和文档。未修改 `main`，未触碰真实 Research Workspace。

### Testing

- `python --version`：本地 `Python 3.13.5`；GitHub Actions 仍固定 `Python 3.11`。
- `python -m compileall -q src`：通过。
- `python -m pytest --collect-only -q`：649 项收集，1 个既有 legacy skip。
- Phase 1 deterministic certification suite（与工作流一致）：`234 passed, 12 deselected, 3 warnings`。
- 必需定向回归：`135 passed, 1 failed, 3 warnings`；唯一失败为既有未跟踪 Durable Contract fixture 导致的 `StopIteration`，且已在工作流排除项中，不是本轮源码失败。
- `tests/research_factory` 全量回归：`329 passed, 33 failed, 1 skipped, 3 warnings`；失败均为既有历史/脱敏合同与 registry fixture 缺失或真实样本分区数据漂移，未修改其范围。
- `git diff --check`：通过；仅有 Git 工作区行尾转换提示。
- 真实 Research Workspace：本轮仅执行只读状态检查，未写入或生成任何文件。

### Notes

- `.github/workflows/phase1-certification.yml`：将 whitespace gate 改为校验 push 提交补丁或 PR cumulative patch，并保持 Python 3.11、编译、收集和 deterministic suite。
- `CLAUDE.md`：补充 Durable Contract hash 冲突和 Receipt 非 READY 语义的施工陷阱。
- `docs/Candidate_Executable_Materialization_Bridge_V1.md`：记录 Receipt schema v2、恢复状态和确认前 hash 对账。
- `docs/Canonical_Authority_and_Objective_Reconciliation_V1.md`：明确 Receipt + Durable Contract 完整对账后才可派生 Structural Ready。
- `docs/PHASE1_CERTIFICATION_BLOCKER_FIXES_V1.md`：补充最终阻断修复、无副作用和工作流门禁说明。
- `src/chanlun_trader/research_factory/candidate_executable_materialization.py`：修复 Receipt 语义、已有合同 hash 冲突 fail-closed 和状态读模型。
- `src/chanlun_trader/research_factory/objective_reconciliation.py`：对账时拒绝与 Preview contract hash 不一致的 Durable Contract。
- `src/chanlun_trader/research_factory/run_budget.py`：拆开嵌套 f-string，恢复 Python 3.11 可编译的确定性 trial identity。
- `tests/research_factory/test_candidate_executable_materialization_v1.py`：新增 Receipt-only 非 READY 与已有合同内容 hash 冲突的无副作用覆盖。
- `progress.md`：追加本轮实施与验证记录。
- 回滚方式：本轮提交后在该分支执行 `git revert --no-edit <本轮 commit SHA>`；不使用 force push，不回写 `main`，不触碰真实 Research Workspace。

## 2026-09-06 - Task: PHASE1_CERTIFICATION_EXACT_MATCH_TEST

### What was done

补充 AC14–AC16 的显式合法场景测试：已有 Durable Contract 的 `candidate_id`、`candidate_hash`、`content_hash` 与 Preview 完全一致时，首次 confirm 复用该合同，重复 confirm exact-once，不新增 Contract/Receipt，且两份 immutable 文件字节保持不变。

### Testing

- `tests/research_factory/test_candidate_executable_materialization_v1.py`：`18 passed, 3 warnings`。
- 与工作流一致的本地 Phase 1 deterministic suite：`235 passed, 12 deselected, 3 warnings`。
- 本轮仅增加测试与进度记录，需由最终 head 的 GitHub Actions 再次确认。

### Notes

- `tests/research_factory/test_candidate_executable_materialization_v1.py`：新增 existing Contract exact-match/idempotent immutability 覆盖。
- `progress.md`：追加 AC14–AC16 验证记录。
- 回滚方式：本轮提交后在该分支执行 `git revert --no-edit <本轮 commit SHA>`；不使用 force push，不回写 `main`，不触碰真实 Research Workspace。

## 2026-09-06 - Task: PHASE1_CERTIFICATION_REMOTE_FINAL_PASS

### What was done

完成最终提交 `7cbb0ea8dbd78c32eaee59078218e2359b258d78` 的远端认证闭环；GitHub Actions `Phase 1 Certification` Run `34015041202` 已在 clean checkout 中通过依赖安装、前端构建、whitespace、Python 3.11 编译、全量收集和 Phase 1 deterministic suite。

### Testing

- Run `34015041202`：`status=completed`、`conclusion=success`，所有 job steps 均为 success，head SHA 与最终提交一致。
- 远端认证日志确认前端 shell 由工作流现场构建，解决 clean checkout 下 webapp smoke tests 的 404；未新增测试排除。
- 远端分支已指向 `7cbb0ea8dbd78c32eaee59078218e2359b258d78`；远端 `main` 仍为 `b49ab4b1fa8cf08929bb7abbcc22c5aca78a9fd7`。

### Notes

- `progress.md`：追加最终远端认证 PASS 证据。
- 回滚方式：本轮提交后在该分支执行 `git revert --no-edit <本轮 commit SHA>`；不使用 force push，不回写 `main`，不触碰真实 Research Workspace。

## 2026-09-06 - Task: PHASE1_CERTIFICATION_CI_FRONTEND_BUILD_REPAIR

### What was done

根据第三次远端运行日志，确认 clean checkout 中 `frontend/dist` 按仓库策略不入库，导致既有 webapp smoke tests 在收集后的认证套件中返回 404；在同一 Phase 1 工作流加入现有前端的 `npm ci && npm run build` 准备步骤，未增加测试排除或改变业务代码。

### Testing

- 远端运行 `34014935550`：依赖安装、whitespace、Python 3.11 compile 和 649 项 test collection 均通过；失败为 6 个既有 webapp shell 断言（`404 != 200`）。
- `frontend/package-lock.json` 与现有 `frontend/package.json` 提供确定性安装入口；新的工作流运行需在 clean checkout 重新验证构建后 webapp shell 可用。

### Notes

- `.github/workflows/phase1-certification.yml`：在 Python 门禁前加入 Node 20、`npm ci` 和前端构建，使被忽略的 dist 仅在 CI 临时生成。
- `progress.md`：追加 clean checkout 前端构建修复记录。
- 回滚方式：本轮提交后在该分支执行 `git revert --no-edit <本轮 commit SHA>`；不使用 force push，不回写 `main`，不触碰真实 Research Workspace。

## 2026-09-06 - Task: PHASE1_CERTIFICATION_CI_JSONSCHEMA_DEPENDENCY_REPAIR

### What was done

依据第二次远端收集日志，补齐 `tests/research_factory/test_autonomous_orchestrator_v2.py` 实际导入的 `jsonschema` 依赖；`pytz` 和 `httpx2` 已确认在 Python 3.11 认证环境成功安装，其他认证范围保持不变。

### Testing

- 远端运行 `34014878807`：依赖安装、whitespace 和 compile 均通过；收集阶段仅剩 `ModuleNotFoundError: jsonschema`。
- 代码与确定性套件在前一提交已通过本地验证；本修复只变更依赖声明和进度记录，需由新的 Python 3.11 Actions 运行完成最终验证。

### Notes

- `requirements.txt`：声明 `jsonschema>=4.20`，覆盖认证测试的实际导入。
- `progress.md`：追加第二个远端 CI 依赖收口记录。
- 回滚方式：本轮提交后在该分支执行 `git revert --no-edit <本轮 commit SHA>`；不使用 force push，不回写 `main`，不触碰真实 Research Workspace。

## 2026-09-06 - Task: PHASE1_CERTIFICATION_CI_DEPENDENCY_REPAIR

### What was done

根据远端 `Phase 1 Certification` 的真实日志，补齐 Python 3.11 认证环境缺失的 `pytz` 运行依赖和 Starlette `TestClient` 所需的 `httpx2` 测试依赖；未改变认证工作流结构、Python 版本、测试范围或任何业务执行路径。

### Testing

- 失败运行 `34014787704` 的 `Install deterministic test dependencies`、whitespace 和 `Compile source` 已通过；失败点明确为 `Collect all tests` 的 `ModuleNotFoundError: pytz` 与 `httpx2` 缺失。
- 本地此前已通过 Phase 1 deterministic suite：`234 passed, 12 deselected, 3 warnings`；依赖补充后需由 GitHub Actions 在 Python 3.11 上重新确认收集与认证。

### Notes

- `requirements.txt`：声明 `pytz` 与 `httpx2`，使认证环境与源码/测试实际导入一致。
- `progress.md`：追加远端 CI 依赖修复与验证记录。
- 回滚方式：本轮提交后在该分支执行 `git revert --no-edit <本轮 commit SHA>`；不使用 force push，不回写 `main`，不触碰真实 Research Workspace。

## 2026-09-06 - Task: PHASE2_AUTONOMOUS_RESEARCH_CONTROL_PLANE_V1

### What was done

基于正式 `main=bca6aa2ebc3861b7a99990fd12b2d13ad4f7addc` 创建 `codex/phase2-autonomous-research-control-plane-v1`，完成 outcome-blind Autonomous Research Control Plane V1：Research Action、Capability Registry、Permission Resolver、Decision Artifact、append-only exact-once/recovery journal、reconciliation-first one-action-per-tick loop、CLI、Console read model 与 local-only tick endpoint。自动动作仅限 Candidate Proposal、Executable Materialization Preview 和已人工确认合同的 recovery；AI approval、Candidate governance freeze、Executable confirmation、Structural Entry、Predictive authorization、Trial、Final、Prospective 与 Real Order 均保留人工/禁止边界。

同步补充 Phase 2 文档、Canonical Authority 边界说明、Phase 2 GitHub Actions、Vue 控制台状态卡片与最小路由回归断言更新；真实 Research Workspace 只执行 inspect/dry-run 读取，未复制、覆盖或写入。

### Testing

- `python -m compileall -q src`：通过。
- `python -m pytest --collect-only -q`：668 项可收集，1 项既有 legacy integration skip。
- Phase 1 deterministic suite：`235 passed, 12 deselected, 3 warnings`。
- Phase 2 control-plane synthetic suite：`18 passed, 3 warnings`。
- `npm run build --silent`：生产构建通过；仅有既有 chunk size warning。
- `git diff --check`：通过，无补丁空白错误。
- CLI 对真实 Research Workspace 执行 `--inspect --json` 与 `--tick --dry-run --json`：均成功且无执行写入；HEAD 与状态哈希保持 `42fbc52a2cc10212288a293e15c630ed09ea59f5` / `35555b3e0caf1defd5ec50a5fe5157b657a18c514cdfc0f97f6d0562fbcd02c9`。
- 前端本地预览已复核新增 Autonomous Research Control Plane 卡片布局；预览期间未启动后端，因此页面 API 读取失败提示属于环境状态，不是构建失败。
- 既有全量 `python -m pytest -q` 基线仍包含缺失 Research Workspace/runtime artifacts 导致的失败；未扩大 Phase 1 排除范围，也未把该结果冒充为回归通过。

### Notes

- `.github/workflows/phase2-control-plane-certification.yml`：新增 Phase 2 Python 3.11、前端构建、收集、Phase 1 回归和 Phase 2 synthetic CI 门禁。
- `docs/PHASE2_AUTONOMOUS_RESEARCH_CONTROL_PLANE_V1.md`：记录 Phase 2 目标、架构、动作、权限、人工闸门、恢复、结果盲化、预测边界、测试与 Phase 3 前置条件。
- `docs/Canonical_Authority_and_Objective_Reconciliation_V1.md`：补充 runtime decision evidence 不具备 canonical authority 的边界。
- `src/chanlun_trader/research_factory/autonomous_action_journal.py`：新增 append-only execution receipt、exact-once、retry 和 crash recovery journal。
- `src/chanlun_trader/research_factory/autonomous_control_plane.py`：新增控制平面、动作模型、能力注册、权限解析、单 tick/loop、CLI 与结果盲化执行边界。
- `src/chanlun_trader/research_factory/canonical_authority.py`：登记 Autonomous Research Decision 为 runtime evidence。
- `src/chanlun_trader/research_console.py`：提供控制平面只读 read model。
- `src/chanlun_trader/webapp.py`：提供控制平面 GET 和 local-only tick POST endpoint。
- `frontend/src/console/ResearchConsole.vue`：新增控制平面状态、权限、回执、预算与单 tick 入口展示。
- `frontend/src/console/api.ts`：新增控制平面读写 API client。
- `frontend/src/console/research-console.css`：新增控制平面卡片布局与窄屏样式。
- `frontend/src/console/types.ts`：新增控制平面 action/decision/view 类型。
- `tests/research_factory/test_agent_capability_registry_v1.py`：覆盖能力声明与自动执行授权分离。
- `tests/research_factory/test_research_action_permission_v1.py`：覆盖 canonical conflict 和人工治理闸门优先级。
- `tests/research_factory/test_autonomous_action_journal_v1.py`：覆盖 exact-once completion 与 STARTED recovery。
- `tests/research_factory/test_autonomous_control_plane_v1.py`：覆盖 one-action tick、stale、dry-run、human gate、materialization recovery、predictive deny 与 outcome-blind。
- `tests/research_factory/test_autonomous_control_plane_web_v1.py`：覆盖 Console GET、local POST dry-run 和结果盲化。
- `tests/research_console/test_research_console_read_boundary_v1.py`：同步新增一读一写两条控制平面路由的边界计数与路径断言。
- `progress.md`：追加本轮 Phase 2 实施与验证记录。
- 回滚方式：本轮提交后在该分支执行 `git revert --no-edit <本轮 commit SHA>`；不删除旧 Phase 1 branch，不修改 `main`，不 force push，不触碰真实 Research Workspace。

## 2026-09-06 - Task: PHASE2_CRASH_RECOVERY_CLOSURE_V1

### What was done

修复 Phase 2 Control Plane 在 `STARTED` receipt 已写入、Candidate Proposal/Materialization domain side effect 已成功、但 `COMPLETED` receipt 尚未写入时的 restart recovery 顺序。新增 identity-bound、canonical-backed、outcome-blind 的 `SideEffectRecoveryEvidenceV1`：恢复识别先于 stale rejection；精确匹配时只补写 `recovered=true` 的 `COMPLETED`，无副作用时才按同一 idempotency identity retry，错误 identity 直接 fail closed。Proposal、Preview、Confirmation、Durable Contract 均复用现有完整性与 Phase 1 materialization reconciliation，不改变 journal 的 runtime evidence 定位或任何人工/预测闸门。

### Testing

- `python -m compileall -q src`：通过。
- `python -m pytest --collect-only -q`：674 项可收集，1 项既有 legacy integration skip。
- Phase 1 deterministic regression suite：`235 passed, 12 deselected, 3 warnings`。
- Phase 2 control-plane suite：`24 passed, 3 warnings`；其中新增/强化 T1–T7 覆盖三类 post-side-effect/pre-COMPLETED crash、wrong identity、safe retry、stale fail-closed 与 completed replay exact-once。
- `npm ci && npm run build`：通过；仅有既有 chunk size warning。
- `git diff --check`：通过。

### Notes

- `src/chanlun_trader/research_factory/autonomous_control_plane.py`：新增 recovery evidence、Action domain identity 绑定、严格 resolver 和 stale 前 recovery 顺序。
- `tests/research_factory/test_autonomous_control_plane_v1.py`：新增 Crash Matrix A–F 的确定性控制面测试，并确认 domain provider 不被重复调用。
- `docs/PHASE2_AUTONOMOUS_RESEARCH_CONTROL_PLANE_V1.md`：记录 identity-bound recovery 规则与 fail-closed 行为。
- `progress.md`：记录本轮实现与本地门禁结果。
- 回滚方式：在该分支执行 `git revert --no-edit HEAD`（当前提交）；不 merge、不 force push、不修改 `main`，不触碰真实 Research Workspace。

## 2026-09-08 - Task: P3-A 执行策略、工作区隔离与安全启动

### What was done

完成本轮 Web 应用组合与只读执行隔离；保留领域确认、身份与 Phase 2 预测禁止边界。状态：本地认证通过，分支 CI 待核对，等待独立复核。设计、入口矩阵及证据见 docs/PHASE3A_EXECUTION_ISOLATION_V1.md；路线规划见 docs/AUTONOMOUS_RESEARCH_MASTER_ROADMAP_V1.md。其他工作包未开始。

### Testing

- 新鲜子进程 import/startup 先验通过；P3-A 66 passed、0 skipped/failed；覆盖 41 条 POST、61 条研究 GET。
- Phase 1 原选择 235 passed、12 deselected、0 skipped/failed；Phase 2 24 passed、0 skipped/failed。
- collect-only 740 collected，另 1 个原有集成模块 skipped；compileall 与 git diff --check 通过。
- npm ci、npm run build 通过；npm test 8 passed、0 skipped/failed。
- 三组执行端探针真实 Predictive/Structural/Codex AI 均为 0；合成模板调用 5/72/19，approve 尝试 1/70/17，confirm 尝试 4/74/17（P3-A/Phase 1/Phase 2）。
- 受保护原始目录访问、业务网络与非 Python 进程探针均为 0；真实运行态未独立核验，不宣称全系统历史计数为 0。
- CI 状态在提交时为 PENDING，最终 run_id/head_sha 由交付回复关联；不以本地结果替代 CI。

### Notes

- .github/workflows/phase1-certification.yml：在依赖安装之外启用隔离探针，先验验证启动并复用原认证选择。
- .github/workflows/phase2-control-plane-certification.yml：在依赖安装之外启用隔离探针，先验验证启动并复用原认证选择。
- .github/workflows/phase3-execution-isolation-certification.yml：在依赖安装之外启用隔离探针，先验验证启动并复用原认证选择。
- CLAUDE.md：记录本轮发现的构造写入与模式规范化约束。
- README.md：说明默认只读启动和显式研究目录参数。
- docs/AUTONOMOUS_RESEARCH_MASTER_ROADMAP_V1.md：原样保存总体规划，明确非运行授权。
- docs/PHASE3A_EXECUTION_ISOLATION_V1.md：记录设计、全部入口矩阵、实际本地结果与未覆盖范围。
- scripts/run_ui.py：保留端口参数并支持显式只读 research root。
- src/chanlun_trader/execution_policy.py：定义不可变策略与工作区路径校验。
- src/chanlun_trader/research/io_safety.py：把导入时目录创建移至显式审计写入。
- src/chanlun_trader/research_console.py：缺少编排状态时返回明确不可用响应。
- src/chanlun_trader/research_daemon_state.py：把构造时目录创建移至显式事件写入。
- src/chanlun_trader/research_factory/autonomous_orchestrator_v2.py：把构造时目录创建移至显式事件写入。
- src/chanlun_trader/webapp.py：按应用绑定服务与任务，移除 startup recovery 并在领域调用前执行策略。
- tests/conftest.py：隔离认证时拦截真实执行端并分别统计合成动作。
- tests/isolation/sitecustomize.py：在 import 前拦截受保护目录访问与业务网络，输出进程计数。
- tests/research/test_predictive_authorization.py：显式注入 synthetic 应用并保留原业务断言。
- tests/research_console/test_research_console_read_boundary_v1.py：显式注入 synthetic 应用并保留原业务断言。
- tests/research_factory/p3a_pending_trial_fixture.py：现场生成待恢复 Trial 的合成合同、政策及授权前置工件。
- tests/research_factory/test_ai_design_approval_v1.py：显式注入 synthetic 应用并保留原业务断言。
- tests/research_factory/test_autonomous_control_plane_web_v1.py：显式注入 synthetic 应用并保留原业务断言。
- tests/research_factory/test_autonomous_orchestrator_v2.py：fixture 显式创建写入目录，不依赖构造副作用。
- tests/research_factory/test_candidate_executable_materialization_v1.py：显式注入 synthetic 应用并保留原业务断言。
- tests/research_factory/test_candidate_generation_governance_v1.py：显式注入 synthetic 应用并保留原业务断言。
- tests/research_factory/test_execution_isolation_v1.py：新增导入、启动、读写路由、双 root、确认与链接逃逸认证。
- tests/research_factory/test_governance_execution_v1.py：显式注入 synthetic 应用并保留原业务断言。
- tests/research_factory/test_research_evolution_ai_design_v1.py：显式注入 synthetic 应用并保留原业务断言。
- tests/research_factory/test_research_proposal_governance_v1.py：显式注入 synthetic 应用并保留原业务断言。
- tests/research_factory/test_safe_runtime_context_v1.py：合成 CLI 子进程继承隔离路径。
- tests/research_factory/test_structural_entry_projection_reconciliation_v1.py：显式注入 synthetic 应用并保留原业务断言。
- tests/test_webapp.py：验证默认 K 线入口拒绝，不再依赖本机行情。
- progress.md：追加本轮状态与证据链接，不改写历史记录。
- 回滚点：7f22237958731fbc5e7803ee41ff40a55a277ff1；本分支本轮提交完成后可执行 git revert --no-edit HEAD。不 reset、不修改 main、不触碰原始研究目录。

## 2026-09-08 - Task: P3-A PR 认证 SonarCloud 阻断最小修复

### What was done

针对 PR-context SonarCloud 暴露的新增工作流依赖未锁定、npm 安装脚本未禁用，以及合成待恢复 fixture 与既有测试重复度过高，完成仅限认证链路的最小修复；未改变 P3-A 运行策略、领域门禁、研究数据或真实执行入口。

### Testing

- `python -m py_compile tests/research_factory/p3a_pending_trial_fixture.py`：通过。
- `python -m pytest -q tests/research_factory/test_execution_isolation_v1.py`：`66 passed`。
- `git diff --check`：通过；远端 PR-context 三条 workflow 与 SonarCloud 结果将在新 HEAD 上重新认证。

### Notes

- `.github/workflows/phase3-execution-isolation-certification.yml`：改用 hash-locked Python 依赖并以 `npm ci --ignore-scripts` 构建。
- `.github/workflows/requirements-p3a.txt`：新增面向 Ubuntu Python 3.11 认证环境的完整 hash 锁定依赖。
- `tests/research_factory/p3a_pending_trial_fixture.py`：保持现场生成合成工件，调整构造表达以消除与历史测试的重复代码。
- `progress.md`：追加本轮 SonarCloud 阻断修复与验证记录。
- 回滚方式：在该分支执行 `git revert --no-edit HEAD` 回到 `6ba8139e640a941409a374108baa7efc7f8c088f`；不 reset、不修改 `main`、不 force push、不 merge。

## 2026-09-08 - Task: P3-B 公共重启恢复、持久意图与单机互斥

### What was done

从已合并 P3-A 的 origin/main 7e277dbe1d20061fd672cf53d1358d07f16a0b1b 创建独立 worktree/分支 codex/phase3b-restart-recovery-v1。完成显式 root+Objective 恢复、持久原 Action、canonical 副作用优先恢复、同 key 安全重试、所有 CP dry-run 只读与相关领域/共享资源互斥。保持默认只读、人工门禁、Web startup recovery 禁用和 outcome-blind。没有启动 P3-C、真实研究、Final Test 或合并操作。详细入口与限制见 docs/PHASE3B_RESTART_RECOVERY_V1.md。

### Testing

- 全新 venv、系统临时目录 synthetic fixture：P3-A 66 passed；Phase 1 235 passed / 12 deselected；Phase 2 24 passed；P3-B 33 passed。
- 最后代码修改后的 P3-B + CP + P3-A 定向复跑：116 passed；禁用 predictive/structural/AI 调用探针、网络、非测试进程调用、受保护目录访问全部为 0。硬退出子进程用 fsync 调用证据验证，不把缺失 atexit 数据记作零。
- compileall 与 git diff --check 通过；全量收集 773 项 / 1 legacy module skipped（不是执行通过数）；前端 8 passed，构建通过。
- Windows Python 3.13.5；fixture 位于 C: NTFS，源码位于 E: exFAT。Linux/Windows Python 3.11 由新工作流认证，提交时 CI=PENDING，交付时以最终 SHA 的远端记录补充。
- 早期系统 Python 有 4 次启动、共 8 次 distribution discovery 访问原目录被隔离钩子阻断；未读到内容，未观察到写入。重新创建 venv 后探针全部为 0。默认镜像 TLS 失败后改官方 PyPI，未禁用 TLS 或 hash 校验。
- 未验证真实工作区运行/历史对账、Final Test、网络文件系统、多主机、断电、POSIX fork 继承分支、PR-context 检查；不声称上述可用。

### Notes

- .github/workflows/phase3b-restart-recovery-certification.yml：增加 Windows/Linux 分支认证矩阵和平台证据。
- .github/workflows/requirements-p3b.txt：补齐 Windows hash-locked 依赖，保留 P3-A 锁定。
- CLAUDE.md：补充恢复、互斥和隔离测试约束。
- docs/AUTONOMOUS_RESEARCH_MASTER_ROADMAP_V1.md：更新 P3-A 合并与 P3-B 待独立复核状态。
- docs/PHASE3B_RESTART_RECOVERY_V1.md：记录协议、入口矩阵、平台证据和验证边界。
- progress.md：追加本轮实施和验证证据。
- src/chanlun_trader/research_daemon.py：相关 canonical 写入口锁定并拒绝 CP 管理目标旁路。
- src/chanlun_trader/research_factory/ai_design_approval.py：将相关显式写入口纳入共享互斥，保留既有领域检查。
- src/chanlun_trader/research_factory/artifact_graph.py：共享图在资源锁内重新加载并合并。
- src/chanlun_trader/research_factory/autonomous_action_journal.py：持久原始 intent、严格证据校验及旧完成兼容。
- src/chanlun_trader/research_factory/autonomous_control_plane.py：公共恢复、Action 校验、只读路径及显式 synthetic 策略。
- src/chanlun_trader/research_factory/autonomous_orchestrator_v2.py：相关写入口锁定并拒绝 CP 管理目标旁路。
- src/chanlun_trader/research_factory/candidate_executable_materialization.py：将相关显式写入口纳入共享互斥，保留既有领域检查。
- src/chanlun_trader/research_factory/candidate_generation.py：将相关显式写入口纳入共享互斥，保留既有领域检查。
- src/chanlun_trader/research_factory/contract_correction.py：将相关显式写入口纳入共享互斥，保留既有领域检查。
- src/chanlun_trader/research_factory/durability.py：共享合同登记在资源锁内读取合并写入。
- src/chanlun_trader/research_factory/mutation_boundary.py：提供不删除锁文件的 Objective 与资源内核锁。
- src/chanlun_trader/research_factory/projection_reconciliation.py：将相关显式写入口纳入共享互斥，保留既有领域检查。
- src/chanlun_trader/research_factory/research_evolution_ai_design.py：将相关显式写入口纳入共享互斥，保留既有领域检查。
- src/chanlun_trader/research_factory/structural_entry.py：将相关显式写入口纳入共享互斥，保留既有领域检查。
- src/chanlun_trader/research_factory/structural_reconciliation.py：将相关显式写入口纳入共享互斥，保留既有领域检查。
- src/chanlun_trader/webapp.py：传递执行策略并将互斥冲突返回 409，保持启动只读。
- tests/isolation/sitecustomize.py：为受阻访问记录调用来源，未放宽隔离。
- tests/research_factory/p3b_process_worker.py：提供真实退出、竞争与领域调用证据 worker。
- tests/research_factory/test_autonomous_control_plane_v1.py：写测试显式注入 synthetic 策略并验证更早身份拒绝。
- tests/research_factory/test_research_action_permission_v1.py：写测试显式注入 synthetic 策略，保留人工门禁。
- tests/research_factory/test_restart_recovery_v1.py：增加 33 项恢复、只读、身份、并发和入口认证。
- 回滚点：7e277dbe1d20061fd672cf53d1358d07f16a0b1b；提交后在独立分支执行 `git revert --no-edit <本轮P3-B提交SHA>`。不 reset main、不 force push、不修改原研究目录；保留新 v2 journal 证据，旧版本不得继续写新 schema。

## 2026-09-08 - Task: P3-B 分支工作流上下文修复

### What was done

修复首次 push CI 解析失败：job-level env 不支持 runner.temp，改为在 Python 启动前通过 RUNNER_TEMP 配置同一受保护参考路径。

### Testing

- GitHub run 34211579523 明确报告 Line 28 Unrecognized named-value runner，jobs 为空；不计为测试失败或通过。
- git diff --check 通过；工作流仍在所有 Python 步骤前配置隔离路径；远端重新解析和矩阵测试等待新提交 CI。

### Notes

- .github/workflows/phase3b-restart-recovery-certification.yml：仅调整受保护路径的设置位置。
- docs/PHASE3B_RESTART_RECOVERY_V1.md：记录首次 CI 配置失败与修复。
- progress.md：追加失败证据和修复记录。
- 回滚方式：提交后执行 `git revert --no-edit <本轮CI修复提交SHA>`，回到 438ffe37ce79f91935e878e5e6c5555bbf82e73a；该回滚会恢复已知无效工作流，不建议用于认证。

## 2026-09-08 - Task: P3-B 平台认证准备步骤修复

### What was done

移除 Windows 失败的非必要 pip cache 探测；将 Linux 会隐式启动子进程的平台查询改为 sys 信息，操作系统信息使用显式 shell 命令。未放宽测试隔离。

### Testing

- run 34211780588：Windows Set up Python 缓存目录探测失败；Linux platform.platform 子进程被拦截，process_calls=1，测试未执行。保留失败记录，不计入通过矩阵。
- git diff --check 通过；本地同隔离环境 sys/tempfile 平台命令通过且三个进程探针均为 0；双平台验证等待修复后 SHA 的 CI。

### Notes

- .github/workflows/phase3b-restart-recovery-certification.yml：移除 pip cache，改为显式无隐式 Python 子进程的平台记录。
- docs/PHASE3B_RESTART_RECOVERY_V1.md：补充第二次 CI 准备阶段失败证据。
- progress.md：追加本轮修复与验证记录。
- 回滚方式：提交后执行 `git revert --no-edit <本轮平台修复提交SHA>`，回到 34c98fb5efa7cf00f5eb817454292904bafd6ccc；会恢复已知认证准备失败，仅作为回滚点。

## 2026-09-08 - Task: P3-B Windows 认证运行时限定

### What was done

Windows CI 选择已在本地认证的 Python 3.13 系列，Linux 保留 3.11；保持全部测试和隔离探针，不给系统版本探测子进程开白名单。

### Testing

- run 34211978686：Linux 全矩阵 success；Windows Python 3.11.9 导入 pandas 所需 platform.machine → win32_ver 调用子进程被拒绝，process_calls=1，未执行测试。
- 本地 Python 3.13.5 同套测试已通过，最终代码后定向 116 passed；git diff --check 通过。新 SHA 的双平台结果由远端 CI 再核对。

### Notes

- .github/workflows/phase3b-restart-recovery-certification.yml：矩阵显式区分 Linux 3.11 和 Windows 3.13。
- docs/PHASE3B_RESTART_RECOVERY_V1.md：记录版本选择依据和 Windows 3.11 未认证限制。
- progress.md：追加本轮运行时限定与证据。
- 回滚方式：提交后执行 `git revert --no-edit <本轮运行时限定提交SHA>`，回到 31ac37981624e88111724498e04285a031271b3d；会恢复已知 Windows 隔离导入失败。

## 2026-09-09 - Task: P3-B FAILED 与 retry marker 恢复记账修正

### What was done

在既有 P3-B 独立 worktree 核对 remote、HEAD fbd603be237cb6077a4484acac9a585610676569、origin/main 7e277dbe1d20061fd672cf53d1358d07f16a0b1b 与干净工作区。先用实际持久 FAILED 和真实 retry marker 后 os._exit 的两个独立进程回归复现 P2，再将重试登记收敛为一次 begin；复用已有 marker，保留历史及全部身份。未修改 journal schema、锁、planner 或研究权限。

### Testing

- 旧生产代码回归：`python -m pytest -q -s tests/research_factory/test_restart_recovery_v1.py -k failed_retry_registers_one_attempt`，2 failed / 33 deselected；实际 FAILED 后恢复到 attempt=3，marker 退出后恢复到 attempt=4，各仅一次恢复调用、一个成功 proposal。完整序列见 P3-B 文档；本地日志 tmp/p3b-evidence/retry-red.log。
- 相同回归修复后 2 passed / 33 deselected；均 STARTED(1) → FAILED(1) → RECOVERY_RETRY_ALLOWED(1) → STARTED(2) → COMPLETED(2)。历史字节前缀、intent 原文与身份不变；四类 dry-run 快照不变。日志 tmp/p3b-evidence/retry-green.log。
- 直接读取现有 P3-B workflow 的认证命令，以独立 venv 运行：P3-A 66 passed；Phase 1 235 passed / 12 deselected；Phase 2 24 passed；P3-B 35 passed；775 collected / 1 既有 legacy module skipped。没有新增 skip/xfail 或放宽排除。
- compileall、git diff --check 通过；npm ci --ignore-scripts、npm test（8 passed）、npm run build 通过。日志位于 tmp/p3b-evidence/retry-*.log（本地忽略文件），远端 CI 提供可共享复验记录。
- Windows Python 3.13.5、本地临时 NTFS synthetic fixture；本轮输出的禁止执行器与进程/网络/受保护目录访问探针均为 0。硬退出子进程采用 fsync marker 事件，不把缺失 atexit 输出记为零。
- 提交时 push/PR CI PENDING。既有分支 PR 查询为空；后续按授权创建 main PR 并核对当前 HEAD、base、checkout 和 required checks。实际 main-merge-governance ruleset 要求 Deterministic governance suite、严格基线与 review thread resolution；不绕过。
- 真实研究/Final Test、Windows Python 3.11、网络文件系统、多主机、断电、POSIX fork 仍未验证。原真实工作区未作为输入输出。没有 merge、auto-merge 或 P3-C。

### Notes

- src/chanlun_trader/research_factory/autonomous_control_plane.py：重试先处理 marker，再唯一 begin。
- tests/research_factory/p3b_process_worker.py：增加可落 FAILED 的受控错误和真实 marker 后退出注入。
- tests/research_factory/test_restart_recovery_v1.py：增加两项失败与 marker 重启回归，验证真实前置回执及历史/身份/副作用/只读。
- docs/PHASE3B_RESTART_RECOVERY_V1.md：追加 P2 实际序列、attempt 口径和历史不回写约束。
- CLAUDE.md：记录重复 begin 陷阱及必须验证持久前置状态。
- progress.md：追加本轮复现、验证和交接事实。
- 回滚：提交后在当前分支执行 `git revert --no-edit <本轮修正SHA>`，回到被复核 HEAD fbd603be237cb6077a4484acac9a585610676569；不重写历史 journal，不 reset/main/force push。

## 2026-09-09 - Task: P3-C 真实生命周期衔接失败复现与核心合同方案

### What was done

核对 origin/main=e50f5abc26bc9aa3b5927c2d5c436f47b3ee3a08、PR #4 已合并、75ec0be 在 ancestry 中，从 main 创建独立 P3-C worktree/分支。完整读取任务附件、总体规划、P3-A/B 文档与进度记录，检查实际规范。通过真实设计、人工批准、CP 生成、人工审核与 Freeze 复现物化失败；没有手工补写下游成功记录，没有生产修改。提供待批准的核心身份/语义合同方案；P3-C 未完成，L1—L10 等后续组合未认证。未 merge、auto-merge 或启动其他包。

### Testing

- 隔离先验 P3-A：66 passed。新正向测试先 1 failed，再用两种初始化明确复现 2 failed；错误为 EXECUTABLE_MATERIALIZATION_INCOMPLETE。补齐初始元数据后仍缺 full_semantic_record/hypothesis，真实 Freeze 已完成，预算原文不变。
- Phase 1 首次 228 passed / 7 failed（6 个前端未构建页面、1 个子进程输出解码错误）；构建后 234 passed / 1 failed；设置 Python UTF-8 后原选择 235 passed / 12 deselected。失败日志保留，未改测试或排除项。
- Phase 2：24 passed；P3-B：35 passed；compileall、diff 检查通过；collect 777 + 1 legacy module skipped（不是 passed）；前端 npm ci --ignore-scripts、8 tests、build 通过。
- Windows Python 3.13.5，全新 venv；fixture 位于 C: 临时目录，Get-Volume 实测 NTFS。Linux 与最终 SHA 双平台结果由分支 CI 核对，提交时 PENDING。
- 本轮两项失败用例探针：合成设计 2、approve 2、confirm 2；approve 内部调用 confirm，不计为四次独立批准。真实禁用执行器、网络、非测试进程、受保护参考目录访问探针均 0。其他尚未建立的 P3-C 探针统计为 NOT_REPORTED，不填零；真实运行态未独立核验。
- 本地日志位于 tmp/p3c-evidence（忽略目录）；共享复现命令及证据范围见 P3-C 文档。未读取真实研究数据或 Final Test，未启动真实 AI/行情/Structural/Predictive/订单。

### Notes

- tests/research_factory/test_phase3c_lifecycle_v1.py：新增真实审批/冻结到物化的两项正向失败验收，不采用 raises/skip/xfail 掩盖断链。
- .github/workflows/phase3c-lifecycle-certification.yml：继承完整回归和平台矩阵，添加 P3-C 阶段、UTF-8 与 fixture 盘结构化文件系统证据。
- docs/PHASE3C_LIFECYCLE_CERTIFICATION_V1.md：记录短审计、失败、待批准方案、计数限制和未完成矩阵。
- docs/AUTONOMOUS_RESEARCH_MASTER_ROADMAP_V1.md：当前状态记录 P3-B 经 PR #4 合并，P3-C 在物化处阻断。
- CLAUDE.md：追加不能用手工下游 fixture 冒充生命周期、不能混用两种候选身份的陷阱。
- progress.md：仅追加本轮记录，保留历史 PENDING 和失败事实。
- 回滚：在独立分支执行 `git revert --no-edit <本轮提交SHA>`，基线 e50f5abc26bc9aa3b5927c2d5c436f47b3ee3a08；不 reset/main/force push，不删除执行历史。

## 2026-09-09 - Task: 记录 P3-C 核心语义合同调整的明确批准

### What was done

记录用户对显式完整语义在审批前确定、校验、哈希绑定并贯通 Candidate/Freeze/Materialization 的批准。复用既有 SemanticCandidateRecord、hypothesis 和语义预注册身份；不覆盖不同层哈希、不升级历史合同、不放宽物化；旧轻量格式保持原校验和阻断。优先修改设计、候选及必要审批/物化衔接，其他生产文件仅限复现必要缺陷。完整范围写入 P3-C 文档。

保留预算、绩效访问、执行权限、planner/journal/锁协议及 P3-A/B 安全边界。授权仅开发与临时合成验证，不授权真实研究、Trial、Final Test、Prospective/Paper、订单，不 merge/auto-merge 或启动后续包；范围内不再重复申请同一批准。

### Testing

- 核对工作区干净，继续同一分支 HEAD 5a14f9a14de2d609186c8532ab671c7fadb99403。
- 只读核对 run 34298279411：双平台均在新增 P3-C 正向物化用例失败，原回归前序步骤成功；保留原失败证据。
- 本条仅批准范围记录，不表示代码已实现或认证通过。

### Notes

- docs/PHASE3C_LIFECYCLE_CERTIFICATION_V1.md：追加用户批准、限制、实施与验证顺序。
- progress.md：追加本次批准记录，保留历史。
- 回滚：提交后 `git revert --no-edit <本条记录所在提交SHA>`；未提交时仅撤销本次追加段落，不改历史记录或 main。

## 2026-09-09 - Task: P3-C 获批完整语义衔接与生命周期认证实现
### What was done
- 在用户本轮明确批准范围内接通 v2 Design → 人工批准 → Candidate → Freeze → Materialization，执行语义审批前固定，各层哈希独立引用，旧轻量路径保持原身份及物化阻断。
- 用自包含临时 Scenario 和真实领域服务完成显式合成 Structural、有效人工 Predictive Authorization；控制平面继续禁止 Trial。实现 L1—L10 新进程矩阵、前效应/重试 marker 硬退出、三入口、投影删除/执行证据损坏、并发、预算及嵌套结果字段负向。
- 保留原语义缺失失败。授权字段篡改先复现 5 failed / 3 passed 后增加 decision_hash 校验；预算快照变更先复现 1 failed 后复用真实 registry.head_hash 校验。安全投影同名 registry_head_hash 语义不同，初次直接比较导致合法授权拒绝，已纠正；没有修改 budget.py 或执行权限。
### Testing
- 本地已取得 Phase 1 235 passed / 12 既有 deselected，Phase 2 24 passed，P3-A 66 passed，P3-B 35 passed；前端 8 tests 与 build 通过（未改前端）。
- P3-C 中间整组 77 passed；随后强化投影实际删除与授权预算快照检查，投影针对性 8 passed、授权针对性 12 passed。当前最终组合及原回归正在重新运行，以后续日志为最终证据，不把这些局部数相加。
- 失败日志保留 tmp/p3c-evidence：auth-identity-red.log、auth-budget-red.log、auth-final.log（初次错误比较两个不同哈希）、p3c-full-first.log、restart-first.log。历史 5a14f9a 的双平台 P3-C run 34298279411 仍为失败证据；原四个 CI 均 success。
- Windows 实测 Python 3.13.5，fixture C:/Users/84219/AppData/Local/Temp，Get-Volume C=NTFS。新的 Linux CI 版本/文件系统和新 HEAD/run_id 尚待取证，CI=PENDING。
- pytest 各运行的 executor/process 探针按日志单列；P3-C 新 worker 在正常返回及硬退出前打印检查点，P3-B 硬退出缺 atexit 的场景不伪写为零。预算负向明确有合成 reserve/consume，不宣称所有测试预算零变动。
### Notes
- 改动文件：
  - src/chanlun_trader/research_factory/research_evolution_ai_design.py：审批前 v2 完整语义校验及设计哈希绑定。
  - src/chanlun_trader/research_factory/ai_design_approval.py：v2 确认时重验设计语义与当前来源。
  - src/chanlun_trader/research_factory/candidate_generation.py：传递获批语义并使用既有语义候选身份，保留 v1。
  - src/chanlun_trader/research_factory/candidate_executable_materialization.py：既有 Proposal 哈希校验识别 v2。
  - src/chanlun_trader/research_factory/autonomous_control_plane.py：验证授权完整性与原始预算快照，修复本轮实际复现的元数据读取缺陷。
  - tests/research_factory/p3c_scenario.py：临时合成初始化及显式人工/领域动作，无第二套状态机。
  - tests/research_factory/p3c_process_worker.py：只接收 root/Objective/operation 的真实服务重建及硬退出探针。
  - tests/research_factory/test_phase3c_lifecycle_v1.py：保留并接通完整合法正向断言，核对语义及各层身份。
  - tests/research_factory/test_phase3c_lifecycle_boundaries_v1.py：完整语义、旧格式、授权、结果盲化与人工门禁负向。
  - tests/research_factory/test_phase3c_restart_v1.py：L1—L10 与 retry attempt、子进程证据检查点。
  - tests/research_factory/test_phase3c_entry_boundaries_v1.py：三入口、实际投影删除、证据损坏、双进程锁与预算边界。
  - tests/research_factory/test_autonomous_control_plane_v1.py：原授权测试改用真实服务生成有效授权，保留原 DENY 断言。
  - .github/workflows/phase3c-lifecycle-certification.yml：追加全套 P3-C 与 JUnit artifact。
  - docs/PHASE3C_LIFECYCLE_CERTIFICATION_V1.md：记录明确批准、身份引用、生命周期矩阵和实测证据。
  - docs/AUTONOMOUS_RESEARCH_MASTER_ROADMAP_V1.md：P3-C 从等待批准更新为实现后认证中。
  - CLAUDE.md：追加本轮语义和授权证据的工程约束。
  - progress.md：仅追加批准及本轮实现/验证记录。
- 回滚点：5a14f9a14de2d609186c8532ab671c7fadb99403（本轮实现前独立分支 HEAD）；实现提交后使用 git revert 撤销该实现提交，不 reset main、不删除研究记录。
- 保持默认 READ_ONLY、无 Web startup recovery、dry-run 只读、P3-B 公共恢复/attempt 语义、单机共享锁、结果盲化及 PHASE2_PREDICTIVE_EXECUTION_DISABLED。
- PHASE3_CLOSED=false；MAIN_MERGED=false；NEXT_PACKAGE_STARTED=false。完成分支认证后交独立复核。

## 2026-09-09 - Task: P3-C 双平台证据归档与独立复核交付
### What was done
- 实现提交 c37fd46c9bfa83968dc1d9ba7effd5d89f4047bd 已推送，真实完整语义链路到人工 Predictive Authorization，保留 Trial 禁令。
- 将用户批准、各层身份、L1—L10、正负向边界、平台和安全计数整理为 P3-C 认证文档；仅同步本次认证状态，不开始其他工作包。
### Testing
- P3-C CI run 34301691534 两平台 SUCCESS：Windows Python 3.13.15 / C: NTFS，84 passed；Linux Python 3.11.16 / /tmp 所在 ext4，84 passed。fixture 根与系统版本详见 P3-C 文档及 job 原始日志。
- 同一 CI 内原回归：P3-A 66、Phase 2 24、P3-B 35 均 passed；Phase 1 Windows 235 passed / 12 deselected，Linux 234 passed / 1 既有 Windows 专用测试 skipped / 12 deselected。collection 859 与 1 继承 module skip 单独记，不算 passed。frontend 8 tests/build、compile、diff 检查通过。
- 同一实现 HEAD 的独立原 CI：Phase 1 34301691757、Phase 2 34301691768、P3-A 34301691558、P3-B 34301691489 全部 SUCCESS。
- 本地 c37fd46 的整套 P3-C 为 84 passed（233.48 秒），原回归再次通过。P3-C 主进程 template/approve/confirm 为 1/61/61；安装的三类执行器与 process 探针均观测 0。worker 检查点和 P3-B 硬退出缺失统计明确区分，未捏造其他未安装的探针。
- 最后三个重哈希授权负向先 3 failed，修正既有身份校验后授权组合 15 passed；暂缓/新预算/重新明确授权正向先 1 failed，修正历史快照覆盖顺序后组合 5 passed。原始日志保留 tmp/p3c-evidence。
- 本次仅文档改动；其新 HEAD 的分支 CI 在提交时 PENDING，最终交付回复补充实际 run_id 与结果。
### Notes
- 改动文件：docs/PHASE3C_LIFECYCLE_CERTIFICATION_V1.md（绑定实现 HEAD、双平台 CI、真实计数与复核停止点）；docs/AUTONOMOUS_RESEARCH_MASTER_ROADMAP_V1.md（P3-C 标记分支认证完成待复核）；progress.md（仅追加本条证据）。
- 回滚代码：git revert --no-edit c37fd46c9bfa83968dc1d9ba7effd5d89f4047bd。文档回滚点为同一 SHA，后续可 git revert 本文档提交；不 reset main。
- 无 merge/auto-merge，无 R1/R2，无真实研究。PHASE3_CLOSED=false；MAIN_MERGED=false；NEXT_PACKAGE_STARTED=false。

## 2026-09-09 - Task: P3-C 授权决定语义一致性修正
### What was done
- 继续现有 P3-C 独立分支，核对 reviewed HEAD 977c64e4a5e57357dd71d5d0551e90be115143d4、main e50f5abc26bc9aa3b5927c2d5c436f47b3ee3a08 及 P3-B ancestry；未创建新工作包。
- 按用户明确批准范围，以真实合成治理链复现 DEFER/END 仅改 status/hash 后错误授权；复用现有决定类型、状态和 next_action 映射，读取矛盾或缺失必需语义时 fail closed。发现并复现同候选缺 candidate_hash 回退旧授权后，仅前移完整性检查。
- 新增公共 inspect/tick、dry-run、重启、最新无效记录不得回退旧授权边界；正常有效授权继续独立受到 PHASE2_PREDICTIVE_EXECUTION_DISABLED。无历史改写、无锁/预算/journal/执行权限变更。
### Testing
- 项目级 red：authorization-consistency-red.log，2 failed / 53 deselected；第二处 red：authorization-missing-hash-red.log，1 failed / 73 deselected。均先复现再修正，原合法正向断言未降级。
- 最终 targeted 21 passed；完整 P3-C 105 passed（298.48 秒），Phase 1 235 passed / 12 deselected，Phase 2 24 passed，P3-A 66 passed，P3-B 35 passed。collect-only 880 collected + 1 个继承 legacy module skip，不能写成 passed。没有新增 skip/xfail/排除项。
- compileall、git diff --check、前端构建、前端 8 tests 成功。Windows Python 3.13.5 / MSC v.1943；fixture 在 C:/Users/84219/AppData/Local/Temp，C: NTFS。原始日志及 JUnit 位于 tmp/p3c-evidence/authorization-final* 和 *-authorization-final.log。
- 五阶段 template/approve/confirm 分别为 P3-A 5/1/4、Phase 1 72/70/74、Phase 2 18/17/17、P3-B 33/33/33、P3-C 1/82/82。安装的真实执行器/网络/非 Python 进程/保护目录探针为 0；P3-B 硬退出缺失 atexit 不写为零。没有全局 Performance/Final Test/订单探针，未取得的统计不作零声明。
- 新提交 push CI、Windows/Linux CI、PR-context CI、required checks 与 SonarCloud 在提交时 PENDING。实际 main ruleset 22374784 要求 strict Deterministic governance suite，无 bypass；创建 PR 后继续验证，最终报告绑定 HEAD/run_id。
### Notes
- 改动文件：src/chanlun_trader/research_factory/predictive_authorization.py（复用既有状态/动作映射与纯语义校验）；src/chanlun_trader/research_factory/autonomous_control_plane.py（无效授权安全标记与完整性检查顺序）；tests/research_factory/test_phase3c_lifecycle_boundaries_v1.py（21 个真实链边界）；docs/PHASE3C_LIFECYCLE_CERTIFICATION_V1.md（短审计、批准范围、red/green 与认证证据）；CLAUDE.md（记录哈希不能替代语义校验）；progress.md（仅追加本轮）。
- 回滚点 977c64e4a5e57357dd71d5d0551e90be115143d4；提交后可在本分支 git revert --no-edit <本次修正提交SHA>，不 reset main、不删除历史记录。
- 用户批准仅为代码协议修正与合成环境验证，不替代领域人工批准；不授权真实研究、Predictive Trial、Final Test、Prospective/Paper 或订单。不 merge/auto-merge，不开始 R1/R2。REAL_WORKSPACE_RUNTIME_INDEPENDENTLY_VERIFIED=NO；PHASE3_CLOSED=false；MAIN_MERGED=false；NEXT_PACKAGE_STARTED=false。

## 2026-09-09 - Task: P3-C 授权一致性修正双平台证据归档
### What was done
- 将修正实现 HEAD d8596efa5331470174877594aed9d70bebb4982c 的双平台原始 CI 结果追加至 P3-C 文档；main 重新 fetch 后仍为 e50f5abc26bc9aa3b5927c2d5c436f47b3ee3a08，工作范围未变。
### Testing
- 五个 push 工作流均 SUCCESS：Phase 1 34305279318；Phase 2 34305279229；P3-A 34305279236；P3-B 34305279245；P3-C 34305279358。
- P3-C Linux job 102320571043：Python 3.11.16 / GCC 13.3.0，/tmp 位于 /dev/root ext4；105 passed（167.24 秒）。Windows job 102320571234：Python 3.13.15 / MSC v.1944，C:/Users/RUNNER~1/AppData/Local/Temp 位于 C: NTFS；105 passed（211.96 秒）。
- Windows Phase 1 235 passed / 12 deselected；Linux 234 passed / 1 原有平台 skipped / 12 deselected；两平台 Phase 2 / P3-A / P3-B 为 24 / 66 / 35 passed；880 collected + 原 legacy module skip。frontend 8 tests/build、compileall、diff 均成功；探针计数与上一条最终日志一致。
- 原文保存在 tmp/p3c-evidence/push-d8596ef-linux.log 和 push-d8596ef-windows.log。本文档提交不修改源代码/测试；其最新 HEAD 的 push 与 PR-context CI、实际 required check 和 SonarCloud 在提交时 PENDING，继续等待并在最终交付报告绑定实际 HEAD/run_id。
### Notes
- 改动文件：docs/PHASE3C_LIFECYCLE_CERTIFICATION_V1.md（追加修正实现 SHA、五个 run_id 和精确平台证据）；progress.md（仅追加本条）。
- 代码回滚：git revert --no-edit d8596efa5331470174877594aed9d70bebb4982c；文档回滚可 git revert 本文档提交。不得 reset main 或改写领域历史。
- 继续创建 P3-C → main PR 供独立复核，不 merge、不启用 auto-merge、不开始 R1/R2。PHASE3_CLOSED=false；MAIN_MERGED=false；NEXT_PACKAGE_STARTED=false；REAL_WORKSPACE_RUNTIME_INDEPENDENTLY_VERIFIED=NO。

## 2026-09-09 - Task: R1 源码来源闭包检查与只读合成数据核验

### What was done

- fetch 核对 origin/main=1f6f29c7ad3e8d9371168dfa3bd5201723b48abd，父提交精确为 e50f5abc/7556b3d；从开发库创建 codex/r1-source-closure-data-readiness-v1 独立干净 worktree。按用户提供的独立复核/PR #5 merge 结论追加 Phase 3 synthetic 工程收尾，保留历史 false/PENDING。
- 两个 corrected 调用点改为部署源码定位，engine hash 不再从数据根取源码；默认 Codex prompt 同样来自部署源码。发布仓库和全部取回历史缺 run_engine_corrected_phase4_v3.py，未编造 helper/算法、未补拿原工作区文件，正式闭包 BLOCKED_MISSING_SOURCE。
- 增加 GuardedResearchReader 显式内存审计、从完整 Durable/政策/因子定义派生需求的只读合成核验；实际读 Parquet、独立日历/状态分母、字段/预热/时点/单位、因子计算图、身份重验和无写测试。完整来源矩阵、数据需求与未覆盖分支见 R1 文档。
- 本轮仅局部 SYNTHETIC_DAILY_INPUTS 认证；原 VOLUME_ACCEL registry 未在发布库中提供，显式测试定义不代表真实公式。corrected parity、真实因子/政策/历史 PIT、事件/PIT_QFQ/完整分钟及真实数据均未认证。R1_IMPLEMENTATION=PARTIAL；REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false。

### Testing

- 全新 .venv，Windows Python 3.13.5；现有 requirements-p3b hash lock 从官方 PyPI 安装，无依赖升级。C: 临时 fixture=NTFS，E: 源码=exFAT。先启用 import 前隔离，再运行 P3-A/collect。
- 原五阶段最终整套：P3-A 66 passed；Phase 1 235 passed / 12 原有 deselected；Phase 2 24 passed；P3-B 35 passed；P3-C 105 passed（303.68 秒）。日志 tmp/r1-evidence/Run-*.log；没有新增 skip/xfail/排除。随后默认 prompt 定位修正的原 backend 专项 15 passed。
- 冷进程复制纯源码/文档/配置，17 模块来源正向、两个缺 corrected loader、缺数据政策、schema/资源正向及缺资源负向。无关键生产模块 mock；业务执行函数被拦截，未 execute/start/resume/recover_all。
- 初版数据夹具 bool/None 类型问题 20 passed / 1 failed，修正后 25 passed。无写子进程发现 tempfile.gettempdir 隐式试写：28 passed / 1 failed；修正后同一用例 1 passed，整组 29 passed。默认 prompt 的空数据根构造先 1 failed，修复后 1 passed；保留所有 red/green 日志。后续最终整组与 collect 结果在交付记录补充，不把中间数量合计为最终 PASS。
- 前端 npm ci --ignore-scripts、build、8 tests 通过；compileall、git diff --check 通过。新增 workflow 继承原五阶段和双平台，增加 R1/JUnit。新 HEAD push 与 Windows/Linux CI 在本条提交前为 PENDING。
- 原回归探针及本轮最终成功测试的禁用执行器、业务网络、非测试进程、受保护内容访问为其安装范围内 0；无全系统历史/Performance/订单计数声明。早期回归辅助脚本误选 pip 安装步骤，环境探测被拦截一次（process_calls=1、exit 79），随后修正仅选 pytest/compile；不是研究执行，日志保留。
- 初始默认目录 Git status 枚举了原目录未跟踪文件名，尝试读取不存在的 AGENTS.md；之后只操作开发库/worktree。未读取真实数据/研究产物/未提交源码，未修改原目录。不得将元数据枚举写为零接触；真实运行态仍 NOT_VERIFIED。

### Notes

- src/chanlun_trader/research_factory/source_dependencies.py：精确定位部署源码，缺原 runner 明确阻断。
- src/chanlun_trader/research_factory/predictive_executor.py：loader 和 engine hash 使用源码根。
- src/chanlun_trader/research_factory/real_runtime.py：复用同一 loader 和源码 hash 路径。
- src/chanlun_trader/research_factory/codex_backend.py：默认正式 prompt 取自部署源码，保留显式注入和执行逻辑。
- src/chanlun_trader/research/io_safety.py：增加显式内存 audit_sink，默认审计落盘行为保留。
- src/chanlun_trader/research_factory/data_readiness.py：候选绑定需求派生、临时合成包只读核验和 JSON CLI。
- tests/research_factory/r1_fixture.py：通过 P3-C 合法链现场生成候选及显式合成数据/定义。
- tests/research_factory/r1_cold_worker.py：新进程真实加载、来源记录和副作用拦截。
- tests/research_factory/test_r1_source_closure.py：纯源码副本的正负闭包与默认 prompt 测试。
- tests/research_factory/test_r1_data_readiness.py：实际 reader、字段/覆盖/身份/时点/无写入正负认证。
- .github/workflows/r1-source-data-certification.yml：继承双平台/五阶段，追加 R1 证据 artifact。
- docs/R1_SOURCE_CLOSURE_AND_DATA_READINESS_V1.md：记录矩阵、部署契约、来源缺口、合成边界与未来最小访问需求。
- docs/AUTONOMOUS_RESEARCH_MASTER_ROADMAP_V1.md：追加 Phase 3 收尾及当前 R1，保留历史状态。
- CLAUDE.md：追加已复现的源码根/数据根及隐式试写陷阱。
- progress.md：仅追加本条实现/验证/回滚证据。
- 回滚点为 main 基线 1f6f29c7ad3e8d9371168dfa3bd5201723b48abd；提交后在独立分支 git revert --no-edit <R1实现提交SHA>，文档提交另行 revert；不 reset main、不删除领域历史。
- 不 merge/auto-merge、不开始 R2，不下载行情、不运行真实 Structural/Predictive、Final Test、Paper/Prospective 或订单。完成后交独立工程复核。

## 2026-09-09 - Task: R1 本地最终验证记录

### What was done

完成 R1 本地工程交付检查，保持正式源码缺口和真实数据 NOT_VERIFIED；准备独立分支推送与双平台认证。

### Testing

- R1 整组 30 passed（58.78 秒），包括真实合成正向、冷加载和缺失/冲突负向；r1-release.log、tmp/r1-results.xml。
- 最终 collect 910 collected，另 1 个继承 legacy module skipped，不计为 passed；compileall、diff --check 通过。
- 原五阶段为 66 / 235（12 deselected）/ 24 / 35 / 105 passed；默认 prompt 变更后另运行原 backend 15 passed。前端 8 tests/build 通过。
- 提交前 Windows/Linux 分支 CI=PENDING，最终 SHA 由 git commit 后核对；未将本地结果冒充远端结果。

### Notes

- progress.md：仅追加最终本地测试数量及证据。
- 回滚：git revert --no-edit <本轮实现提交SHA>；不改 main，不删除历史或真实数据。

## 2026-09-09 - Task: R1 合同政策及因子引用一致性收口

### What was done

在 08fdd307481c058d7d4a1384e14950ded93945ca 已推送后，复查发现诊断包文件哈希不能替代合同引用一致性。先复现重哈希合同指向不同政策仍返回局部 READY，再要求合同 policy_id/version/hash 和 registry hash 与实际加载引用完全一致。测试初始 Objective 在首次设计前声明这些合成引用，经原审批链产生合同；不回写历史批准/合同，不改变治理协议。

### Testing

- r1-policy-ref-red.log：1 failed，证明旧诊断错误接受政策引用；修复后的合法正向、政策负向、缺定义组合 3 passed。
- 新增重哈希 registry 引用负向后，最终 R1 整组 32 passed（62.61 秒），r1-identity-final.log / tmp/r1-results.xml；collect 912 collected + 1 继承 legacy module skipped；compileall、git diff --check 通过。
- 前一实现已取得本地原五阶段 66 / 235（12 deselected）/ 24 / 35 / 105 passed，backend 15 passed、前端 8 tests/build；本次仅诊断校验/fixture 修正，原回归由新 HEAD 双平台 CI 全量再跑。
- 08fdd307 的六个分支 CI 已启动，其中 Phase 1/2/P3-A 已 SUCCESS，其他运行中；这些不替代本次新 SHA 的结果，新提交 CI=PENDING。

### Notes

- src/chanlun_trader/research_factory/data_readiness.py：校验 Objective 存在及合同政策/registry 引用一致性。
- tests/research_factory/r1_fixture.py：首次设计前绑定合成政策和 registry 内容身份。
- tests/research_factory/test_r1_data_readiness.py：重哈希政策与 registry 引用冲突负向。
- docs/R1_SOURCE_CLOSURE_AND_DATA_READINESS_V1.md：说明初始引用、red/green 及证据 SHA 区别。
- progress.md：追加本轮修正证据。
- 回滚：git revert --no-edit <本次修正提交SHA> 回到 08fdd307；不 reset main，不改真实工件。
- 正式 corrected 源码仍 BLOCKED_MISSING_SOURCE，真实数据 NOT_VERIFIED；不 merge/auto-merge，不开始 R2。

## 2026-09-09 - Task: R1 零价格修正与部分工程 PR 认证

### What was done

继续当前 R1，在干净的 6aa0990cc5841a2203cf7517a364fcc7fd88e259 分支核对 origin/main=1f6f29c7ad3e8d9371168dfa3bd5201723b48abd。先用合法 fixture、真实 Parquet 和公共 inspect_dataset 复现全零 OHLC 与单独 low=0 被错误接受，再增加两行生产价格域校验。只改变测试行情行，未改变合同、政策、因子定义或证券状态。保留 volume/amount 有限非负语义、既有校验、源码缺失阻断与真实数据 NOT_VERIFIED。

### Testing

- 项目级 red：2 failed（5.71 秒），实际错误返回 SYNTHETIC_SCOPE_READY，生产代码仍为 6aa0990；其他输入文件内容/mtime 不变。tmp/r1-price-evidence/red.log。
- R1 green：63 passed（124.92 秒），覆盖合法正向、零价格、OHLCVA 负数/NaN/正负无穷、零成交活动、非法包络，以及独立进程禁止写入的正负向。核验前后文件内容/mtime 和目录集合不变；green.log、r1-results.xml。日志 SHA256 已记入 R1 文档。
- 原五阶段完整本地回归：P3-A 66 passed；Phase 1 235 passed / 12 deselected；Phase 2 24 passed；P3-B 35 passed；P3-C 105 passed（299.46 秒）。从原工作流逐条执行 pytest/compileall，未更改选择器。
- collect 943，保留 1 个 legacy 模块 skip；compileall、diff --check 通过；前端 8 tests 和 build 通过，原 bundle 大小警告保留。没有新增 skip/xfail/排除或升级依赖。
- 独立 venv Python 3.13.5，Pandas 3.0.5、NumPy 2.4.6、PyArrow 25.0.1、pytest 9.1.1。R1 网络/进程/保护目录访问与真实 Predictive/Structural/AI 调用计数均为零；合成审批/确认各 59 次。所有本次日志在 tmp/r1-price-evidence，未覆盖旧证据。
- 提交时新 HEAD 的 push CI、PR-context CI、required checks 与 SonarCloud=PENDING。六条 push 工作流通过后创建/复用部分交付 PR；实际 SHA、run/job、PR base/head/checkout 和远端结果在最终报告及 PR 正文记录，不借用旧 HEAD 结果。

### Notes

- src/chanlun_trader/research_factory/data_readiness.py：增加日线价格严格正数校验，原有限性/负数检查保留。
- tests/research_factory/test_r1_data_readiness.py：公共入口价格 red/green、数值边界、零成交活动、包络及无写入子进程负向。
- docs/R1_SOURCE_CLOSURE_AND_DATA_READINESS_V1.md：追加价格语义、可复核 red/green 哈希和部分 PR 认证边界，保留旧历史。
- CLAUDE.md：追加日线价格与成交活动语义区别及公共入口复现要求。
- progress.md：仅追加本轮实现、测试、远端待验证和回滚说明。
- 回滚点为 6aa0990cc5841a2203cf7517a364fcc7fd88e259；提交后在本独立分支执行 git revert --no-edit <本次价格修正提交SHA>。不 reset main，不删除历史证据或用户改动。
- FORMAL_SOURCE_DEPENDENCY_CLOSURE=BLOCKED_MISSING_SOURCE；REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false。没有读取真实研究数据/历史产物，没有真实运行，没有 merge/auto-merge，没有开始 R2；完成后停止并交独立复核。

## 2026-09-09 - Task: R1 可信源码限定调查与恢复阻断交付

### What was done

核对实际 main 与 PR #6 普通合并事实，基线 d3dcb68934ea8fb058c98039181d29894b6425df、第二父提交 404561c8867fdb27b01879ce553bac5e9079428f 均符合预期。在原目录之外从远端新建独立克隆 E:/llmwiki/trade-r1-trusted-source-recovery-v1 及 codex/r1-trusted-source-recovery-v1 分支，未更新原仓库引用。完整读取附件、规范、指定文档和进度历史。

仅查询授权原路径的 Git 元数据：固定导出提交存在、非 shallow，但 corrected 精确路径在固定 tree、本地 --all 可达历史及固定提交祖先均无结果。记录精确缺项、文件级恢复计划和全部已知调用接口；不假定工作区副本不存在。恢复文件 0、适配文件 0；生产代码、测试、CI 和依赖锁未改。未 import/执行原项目、未读取未跟踪文件或任何真实数据，未扫描备份。

### Testing

- Git 命令成功：cat-file 返回 commit；ls-tree 和两项精确路径 log 均空；is-shallow-repository=false。PR #6 state=MERGED、mergeCommit/headRefOid 与基线一致。
- 本地文档差异、仅追加历史和 git diff --check 验证；未将来源缺失写成成功冷加载。既有缺模块负向全部保留，没有替身或新增 skip。
- 按用户要求，推送后使用既有双平台 R1 CI 执行原五阶段与 R1 回归；提交时 WINDOWS_CI=PENDING、LINUX_CI=PENDING，实际最终 SHA/run/job 及结果在最终交付回复报告。不借用 404561c 的旧结果，不在系统 Python 中加载项目。
- corrected 真实 loader 成功正向、内部延迟 import/资源、恢复后的顶层副作用探针均 NOT_VERIFIED，因原脚本未取得；不声明源码闭包 PASS 或未知探针计数为零。

### Notes

- docs/R1_TRUSTED_SOURCE_RECOVERY_V1.md：新增限定 Git 证据、来源清单、接口矩阵、文件级恢复计划、最小补充资料和停止点。
- docs/R1_SOURCE_CLOSURE_AND_DATA_READINESS_V1.md：仅追加 PR #6 收尾及本轮来源阻断链接。
- docs/AUTONOMOUS_RESEARCH_MASTER_ROADMAP_V1.md：仅追加已合并 R1 部分交付及继续阻断、不开始 R2 的状态。
- progress.md：仅追加本轮操作、验证缺口与回滚记录。
- 回滚：在本独立分支执行 git revert --no-edit <本轮文档提交SHA>；本轮基线 d3dcb68934ea8fb058c98039181d29894b6425df。不 reset main，不修改原项目。
- ORIGINAL_REPO_ACCESS=SCOPED_SOURCE_AND_GIT_READ_ONLY；FORMAL_SOURCE_DEPENDENCY_CLOSURE=BLOCKED_MISSING_SOURCE；REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；R2_STARTED=false；MAIN_MERGED=false（本轮分支）。完成本轮交付后停止，等待独立工程复核及精确源码来源补充。
## 2026-09-09 - Task: R1 来源调查分支双平台结果归档

### What was done

提交并推送 cb31d9f76ab8983e69007d89fb6868a17e7ead6b，完成来源调查交付的分支验证；追加实际 run/job、部分成功与独立失败证据，未恢复源码或更改测试。

### Testing

- cb31d9f 的 R1 run 34320730582 双平台 SUCCESS：Linux Python 3.11.16/ext4、Windows Python 3.13.15/C: NTFS；P3-A 66、Phase 2 24、P3-B 35、P3-C 105、R1 63 passed；Phase 1 Windows 235/12 deselected，Linux 234/1 skipped/12 deselected。943 collected、前端/compile/diff 成功，未扩大排除。
- 17 模块冷加载、两处 corrected 缺源码阻断和资源正负向继续成立，真实 corrected 成功正向 NOT_VERIFIED。R1 安装范围内禁用执行器/网络/非测试进程/保护目录计数为 0，合成审批与确认各 59。
- 独立 Phase 1/2/P3-A/P3-B 均成功；独立 P3-C run 34320730603 Windows 为 104 passed/1 failed，L1 dry_run 子进程被 RESEARCH_PROCESS_DISABLED 阻断、exit=79、process_calls=1，具体触发根因未验证。Linux 成功。不抹除该失败，不称全部 CI 全绿，不放宽隔离。
- 原日志保存 tmp/r1-recovery-evidence，详细 job/link 见调查报告。Git 推送 TLS EOF 后以命令级 schannel 保持验证成功；远端分支 SHA 已核对，main 仍为 d3dcb68934ea8fb058c98039181d29894b6425df。
- 本次只追加证据文档；归档新 HEAD 的 WINDOWS_CI/LINUX_CI 在提交时 PENDING，不以 cb31d9f 结果替代。源码/测试相同仅说明差异范围，不等同于新 SHA CI 成功。

### Notes

- docs/R1_TRUSTED_SOURCE_RECOVERY_V1.md：追加 cb31d9f 双平台成功范围、独立 P3-C 失败和证据绑定。
- progress.md：仅追加本次验证归档。
- 回滚本归档：git revert --no-edit <本归档提交SHA>；回滚来源调查：git revert --no-edit cb31d9f76ab8983e69007d89fb6868a17e7ead6b。不 reset main、不改原项目。
- 保持 BLOCKED_MISSING_SOURCE、REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED、READY_FOR_REAL_TRIAL=false、R1_FULLY_CLOSED=false。停止并等待独立工程复核；无 main merge/auto-merge，无 R2。
## 2026-09-09 - Task: R1 精确当前源码快照与静态接入审查

### What was done

在已有独立分支固定 corrected、legacy 与明确静态依赖共 17 个原字节源码副本，来源统一 LOCAL_WORKTREE_SNAPSHOT，历史 UNVERIFIED。形成来源矩阵、导入副作用和最小接入计划；未恢复生产源码或改变 loader。

### Testing

- 原路径和父路径普通文件/目录检查通过，无重解析点；精确 ls-files/check-ignore 无匹配，未重复历史查询。
- 主快照 48823 字节，SHA256 fa7d5439e6f152e4ac6eceffbf29fab5d0d51a5fac3e487afc1542f4384063e1；17 个副本均经 AST 解析，未 import/执行。原始字节与静态读取证据留在忽略目录 tmp/r1-source-review。
- 15 个包模块中 14 个仅行尾差异或原字节相同；io_safety 旧副本含顶层 mkdir、缺 audit_sink，不覆盖当前已认证模块。传递依赖原副本和完整隔离加载/数值正确性未验证。
- 起始 023e656 六条 CI 均 completed/success；cb31d9f 的旧 Windows 失败继续 OPEN，不被后续成功抹除。main 实际仍 d3dcb68934ea8fb058c98039181d29894b6425df。
- git diff --check 通过；没有读取数据/合同/绩效/凭据或运行研究。首次内联 AST 命令发生引号语法错误，改为独立工具脚本成功；未执行目标源码。

### Notes

- docs/R1_LOCAL_SOURCE_REVIEW_V1.md：新增真实快照来源、静态依赖与兼容性审查、接入计划。
- progress.md：仅追加本任务记录。
- tmp/r1-source-review/（Git 忽略，不推送）：保存 17 份原字节、manifest、静态清单与一次性解析脚本。
- 回滚提交：git revert --no-edit <本源码调查提交SHA>；回滚点 023e656ae2fc2d8efb2146a73dc8ca41647ba80d。忽略证据可保留，不涉及原目录。
- REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false。无 merge/auto-merge/R2。
## 2026-09-09 - Task: R1 Windows 失败诊断与有上限定向复现

### What was done

在原进程拒绝点追加脱敏程序与 file/line/function 调用栈；P3-C 在断言前保存完整原始字节 stdout/stderr，两个 CI 工作流失败时也上传独立 synthetic 证据。保留 cb31d9f 的失败与同 SHA 综合成功，根因仍 OPEN，未修生产逻辑。

### Testing

- 三次冷启动 L1：各 1 passed，20.33/22.30/19.70 秒；共 27 个子进程证据，三个 dry_run 均 RAW_BYTES/exit=0。NOT_REPRODUCED_IN_3_ATTEMPTS，不扩大重试次数。
- 本地新 venv Python 3.13.5 按既有 hash 锁安装；默认镜像 TLS 失败，官方 PyPI 保持证书与 hash 验证安装成功。旧 CI 是 3.13.15，记录 patch 差异，不声称同环境复现。
- 完整受影响回归（隔离先验、P3-C 四模块、新诊断）：172 passed / 1 既有弃用 warning，351.28s。主进程 network/process/protected 与三类禁用执行器为 0，合成审批83/确认86，不外推到真实研究。
- 新诊断首轮断言因 Windows argv 字符串表示失败，修正断言后通过；最终继承父保护根的定向验证另 1 passed / 0.50s。故意不存在的 synthetic 程序在创建前被拒绝，exit=79/process_calls=1/原异常/脱敏调用栈/原字节归档均确认，未修改拒绝规则。此为诊断测试，不是旧 L1 根因 red/green。
- 旧两个 Windows job 完整日志：image windows-2025-vs2026/20260824.214.3、CPython3.13.15、cwd、安装包列表、隔离变量与 P3-C 前次序一致；旧失败仍无 executable/完整栈，ROOT_CAUSE_UNCONFIRMED。
- AST/YAML 解析与 git diff --check 通过；审查确认未改白名单、计数递增、原异常和非零退出，无 skip/xfail，无生产语义变更。最终 SHA CI 在推送后单独读取，不以本地或旧 SHA 结果替代。

### Notes

- tests/isolation/sitecustomize.py：仅追加拒绝事件的脱敏 stderr 诊断。
- tests/research_factory/test_phase3c_restart_v1.py：断言前独立证据归档；P3-C 二进制管道，P3-B 文本证据明确标识。
- tests/research_factory/test_process_diagnostics_v1.py：新增合成拒绝、脱敏栈与失败前原字节保存回归。
- .github/workflows/phase3c-lifecycle-certification.yml：独立外部证据路径、always 上传、新诊断用例。
- .github/workflows/r1-source-data-certification.yml：同步同一证据路径、上传和用例。
- docs/R1_WINDOWS_PROCESS_DIAGNOSTICS_V1.md：旧失败、对照、三次复现、诊断合同与 OPEN 边界。
- CLAUDE.md：追加 Windows argv 表示与证据归档约束。
- progress.md：仅追加本任务记录。
- tmp/r1-ci-diagnostics/（Git 忽略）：所有旧日志、冷启动结果、受影响回归与后续 final-ci.json；不推送源码快照或原文。
- 回滚：git revert --no-edit <本诊断提交SHA>，诊断前回滚点 4a25f5e；源码调查独立提交不混入。无 main merge/auto-merge，无 R2 或真实研究。
- REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false。
## 2026-09-09 - Task: 修正诊断工作流上下文错误

### What was done

488d204 的 P3-C/R1 在运行前被 GitHub 判为无效 workflow。将证据目录从 job.env 的 runner.temp 表达式移到已有配置步骤，通过 RUNNER_TEMP 写 GITHUB_ENV；隔离配置、测试和上传不变。

### Testing

- red：P3-C run 34324090959、R1 run 34324091851 为 failure、无 job。GitHub 注释明确 Line32 Col37 Unrecognized named-value runner；不是旧 L1 失败复发。
- GitHub 官方 Context availability 说明 job.env 不支持 runner，step 支持；YAML 解析本身不足以检验 GitHub 表达式上下文。
- 修正复用既有保护根配置步骤的 RUNNER_TEMP/GITHUB_ENV 写法；git diff --check 与 YAML 解析通过。green 以新 SHA 实际创建作业并完成 CI 为准，后续状态只存 tmp/r1-ci-diagnostics/final-ci.json。

### Notes

- .github/workflows/phase3c-lifecycle-certification.yml：证据目录在运行步骤设置。
- .github/workflows/r1-source-data-certification.yml：同步相同设置。
- docs/R1_WINDOWS_PROCESS_DIAGNOSTICS_V1.md：追加本轮配置失败与修正依据。
- CLAUDE.md：追加 job.env 上下文限制。
- progress.md：仅追加本轮失败与修正，不覆盖先前记录。
- 回滚：git revert --no-edit <本配置修正SHA>；前一 SHA 488d2045f14bce2fcad7a418aa4f13a0ac18db79（该点有已知 workflow 配置错误）。本次是实际配置修复提交，不是状态更新提交。
## 2026-09-09 - Task: 清除未成功删除的 job.env 旧表达式

### What was done

上轮字符串替换未匹配 CRLF，添加步骤后仍残留 job.env 旧行，312a460 的 P3-C 34324256129 / R1 34324255347 再次在运行前失败。用精确补丁删除两处残留表达式，不改变诊断、测试与隔离。

### Testing

- GitHub 注释再次明确 Line32 runner 不可用；读取已提交 YAML 确认残留，而非重复运行到绿。
- YAML 结构检查 job.env 无 CHANLUN_PROCESS_EVIDENCE_DIR，配置 step 含 RUNNER_TEMP/GITHUB_ENV，上传 step 仍指向 runner.temp；diff --check 通过。后续 CI 绑定新 SHA。

### Notes

- .github/workflows/phase3c-lifecycle-certification.yml：删除一行无效旧表达式。
- .github/workflows/r1-source-data-certification.yml：删除同一残留行。
- progress.md：追加本次实际修正及失败保留。
- 回滚 git revert --no-edit <本提交SHA> 会回到已知无效配置，不建议部署该回滚点。旧 L1 根因仍 OPEN；不改变研究权限。
## 2026-09-09 - Task: R1 已有源码快照独立复核交付
### What was done
按固定清单校验并打包 17 份原字节源码，附原清单、原提交报告、逻辑路径映射、固定 HEAD 静态比较、旧 io_safety 精确差异与接入定位。来源 LOCAL_WORKTREE_SNAPSHOT，历史 UNVERIFIED。
### Testing
固定 HEAD d433709b01289097f89eb24d19c9f147e38ca985；清单/报告/字节数/SHA256 17/17 通过；主脚本与 legacy 指定指纹通过；ZIP 29 成员无缺项、额外项、重名，逐成员字节及 SHA256SUMS 通过；重解析点检查通过；有限静态敏感规则未命中（不构成认证）。未执行或 import 源码，未运行 CI。ZIP SHA256：af9643db56a3218a40ad6b5f12fa50613f076e8f371f9f3b5641bffafb4aed58。
### Notes
- tmp/R1_SOURCE_SNAPSHOT_REVIEW_d433709.zip：新增 Git 忽略的本地纯源码审查包，内含文档与验证材料。
- progress.md：仅追加本轮交付记录。
回滚：Remove-Item -LiteralPath 'E:\llmwiki\trade-r1-trusted-source-recovery-v1\tmp\R1_SOURCE_SNAPSHOT_REVIEW_d433709.zip'；进度记录保留，追加撤销说明即可。
真实数据 NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；Windows L1 OPEN / ROOT_CAUSE_UNCONFIRMED；未接入、未提交推送、未 merge、未开始 R2。

## 2026-09-09 - Task: R1 已固定快照限定接入与合成回归
### What was done
将两个缺失脚本的必要调用子图接入部署树，隔离历史批次入口，分离代码/数据/证据根。对 F01–F04 取得真实项目 red/green，限定支持 DAILY/RAW 合成合同。保留当前 io_safety、原 Windows L1 OPEN、全部冻结治理和研究边界。已有快照交付日志原文保留。
### Testing
- 原固定副本 17/17 字节/哈希匹配；主脚本、helper 和 ZIP 均匹配指定指纹。来源清单记录本机原字节与 LF 部署文本的不同哈希。
- 首轮 F01 red，其余在 git 进程调用前拒绝（process_calls=3）；显式传递 UNKNOWN 源码身份后 F01–F04 全部真实 red，再最小修正为 4 passed。
- 扩展 fixture/精度口径失败保留；公开 helper 导入清理错误导致一次 26 failed/80 passed，恢复真实导出后定向通过。未用 mock 替代核心组件，未放宽隔离。
- 独立 .venv Python 3.13.5；完整 R1 109 passed（136.87s）；最终新增索引0/100纯指标和完整来源输出后，受影响定向+冷加载51 passed（18.58s）。其中真实合成引擎调用29次，另有真实 Ledger/Fill 指标用例2个。最终执行器/网络/进程/保护路径探针均0；完整R1现有合成审批/确认各59次。
- git diff --check；冷加载源树/数据哨兵/缺corrected/helper/prompt通过。CI提交时PENDING，最终SHA的既有双平台分支与PR认证单独记录，不借用旧SHA。
### Notes
- scripts/run_engine_corrected_phase4_v3.py：提取限定runner/指标，修复排名、退出延迟和成本压力；历史入口拒绝，输出根显式。
- scripts/run_automated_strategy_validation_v1_rerun_v2.py：提取必要helper，双来源PIT缺证据拒绝，去除历史基准路径和固定身份。
- src/chanlun_trader/research_factory/source_dependencies.py：两个部署脚本按明确路径加载，不用同名缓存。
- src/chanlun_trader/engine/engine.py：构造参数传递来源身份；EngineConfig和交易逻辑不变。
- src/chanlun_trader/research/run_manifest.py：显式来源身份可免隐式git；默认行为不变。
- tests/research_factory/test_r1_snapshot_integration.py：新增真实编译器/引擎/账务定向与边界矩阵。
- tests/research_factory/test_r1_source_closure.py：真实正向与独立缺失部署副本负向。
- tests/research_factory/r1_cold_worker.py：核验两个脚本及所有加载包的实际来源，保留无副作用探针。
- .github/workflows/r1-source-data-certification.yml：原CI添加本轮定向文件，原选择器和白名单不变。
- docs/R1_SNAPSHOT_INTEGRATION_V1.md：记录支持/阻断矩阵、红绿证据、失败和未覆盖范围。
- docs/R1_SNAPSHOT_INTEGRATION_SOURCE_MANIFEST.json：记录原快照和适配后的内容身份。
- CLAUDE.md：追加动态helper公开导出及显式来源身份注意事项。
- progress.md：仅追加本轮施工与验证记录。
- tmp/r1-snapshot-*.log、tmp/r1-integration-local-final.log、tmp/r1-targeted-final.log、tmp/r1-integration-local.xml、tmp/r1-targeted-final.xml：忽略目录保留实际失败及成功证据，逐项见本轮文档。
回滚：git revert --no-edit <本轮实现提交SHA>；回滚点d433709b01289097f89eb24d19c9f147e38ca985。日志保留，撤销时另行追加记录；不reset、不修改原研究目录。HISTORICAL_PROVENANCE=UNVERIFIED；真实数据NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；未merge、未开始R2。

## 2026-09-09 - Task: R1 容量合同精确比较与可靠性门禁修正
### What was done
根据061daee的Sonar python:S1244实际告警，将新增容量合同校验改成十进制精确比较，仍只接受冻结0.10，不引入容差或改变成本/成交模型。其余维护性告警保持待审，不扩展重构。
### Testing
47 passed（5.16s），真实合成engine.run=29，禁用执行器/网络/进程/保护路径探针0；新增0.1000000001必须拒绝，原0.10合成执行继续通过。日志tmp/r1-decimal-contract.log。旧PR Sonar reliability=C失败保留；新SHA双平台/PR检查待取证，不借用旧SHA。
### Notes
- scripts/run_engine_corrected_phase4_v3.py：Decimal精确比较容量契约，无epsilon。
- tests/research_factory/test_r1_snapshot_integration.py：新增微小偏移合同的拒绝验证。
- docs/R1_SNAPSHOT_INTEGRATION_SOURCE_MANIFEST.json：更新该脚本实际适配字节/文本哈希。
- docs/R1_SNAPSHOT_INTEGRATION_V1.md：追加告警原因、修正和验证。
- progress.md：追加本轮实际门禁修正记录。
- tmp/r1-decimal-contract.log：保存定向结果。
回滚：git revert --no-edit <本提交SHA>；回滚点061daee78d910442d1ed021cba6b66bc6227edb7（有已知Sonar告警）。真实数据NOT_VERIFIED，READY_FOR_REAL_TRIAL=false，R1_FULLY_CLOSED=false，Windows L1 OPEN，未merge、未开始R2。

## 2026-09-10 - Task: 原锁依赖恢复与 R1 available_at 空值最小修正

### What was done

核验官方原 wheel 和锁哈希，修正本轮安装命令遗漏 /simple 的索引路径，在全新 venv 完成原完整锁安装；未改全局网络、TLS 或依赖锁。恢复隔离后取得真实部署 loader/runner 的入场和已持仓结构退出 red，在共享适配行入口拒绝缺失及解析后 NaT，保留原时区、合法未来时间和固定持有语义，追加明确诊断计数。复用 PR #7，保持 Draft。

### Testing

- 官方 JSON、Simple 和 wheel HTTP 200；此前错误包路径 HTTP 404。wheel 5302 bytes、SHA256 117bac03a25ede5df5440e855b32d556049ca169ead221505badf432fed4b101 匹配原锁。默认镜像 TLS EOF 根因仍未确认。
- Python 3.13.5/pip 25.1.1；原 requirements-p3b.txt 递归完整哈希安装成功，pip check 通过，原锁字节哈希不变。pytdx 使用 pip 接受的缓存构建 wheel，不声称全套构建可复现。
- 隔离先验 66 passed；原正向/未来/缺列验证 2 passed、engine 2 次。真实 protected root 为原研究工作区，未读取真实数据。
- 项目 red 13 项入场放行和 5 项未知时间结构退出；原生 datetime64[us, Asia/Shanghai] 的 None/NaN 实际均为 NaT，object 原值另有逐格记录。不可解析文本的原解析拒绝不计 red。
- 首轮 28 failed/6 passed、engine 27 次中包含 7 项固定持有 fixture 错配；保持生产校验，修正测试装配后补跑 6 failed/1 passed、engine 7 次（5 项缺新诊断、1 项原解析拒绝），不将这些计作业务放行 red。原始失败日志保留。
- 修正后定向 81 passed、engine 63 次；完整 R1 146 passed/146.65s、engine 63 次，合成审批/确认各59次。原 F01–F04、loader 正向/缺依赖负向及源根毒化未删除。新用例 DataFrame/只读 fixture 哈希不变，写打开/禁用执行器/网络/进程/保护访问计数为0。五种合法时间完整信号/订单摘要/退出决定红绿相同，空值固定持有首次退出相同。
- git diff --check 通过；局部顺序复核确认共享 compiler/时间函数、治理、进程白名单未变。最终新 HEAD 的五阶段/R1 分支与 PR 双平台 CI 待推送取证，状态只写独立证据和 PR，不沿用旧结果。

### Notes

- scripts/run_engine_corrected_phase4_v3.py：拒绝无效因子时间行并计数，同时覆盖入场和结构退出。
- tests/research_factory/test_r1_snapshot_integration.py：保留原测试，新增34项时间边界回归和逐例输入/运行证据。
- docs/R1_SNAPSHOT_INTEGRATION_SOURCE_MANIFEST.json：更新本轮适配脚本的实际字节与 LF 哈希。
- docs/R1_AVAILABLE_AT_NULL_FIX_V1.md：记录依赖恢复、真实 red/green、范围和回滚。
- CLAUDE.md：追加 NaT 与官方索引路径的本轮经验。
- progress.md：仅追加本轮记录。
- E:/llmwiki/r1-dependency-evidence（仓库外）：脱敏安装诊断、官方元数据、原 wheel、全部失败及成功测试日志、XML、逐例比较与后续 CI 证据。
- 回滚：git revert <本轮修正提交SHA>；回滚点 f4a3681ccf8d44c71bcfa71bf04a0ea2134a5539；不 reset 或覆盖旧日志。
- HISTORICAL_WINDOWS_L1_INCIDENT=OPEN_ROOT_CAUSE_UNCONFIRMED；HISTORICAL_PROVENANCE=UNVERIFIED；REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；未merge、未auto-merge、未退出Draft、未开始R2。

## 2026-09-10 - Task: 清理本轮新增测试的五处复合断言提示

### What was done

725eb55 的 Sonar gate 通过，总29条提示中有5条来自本轮新增测试。将这5处复合断言拆成独立断言，使失败位置更明确；保持测试内容、生产代码及原24条问题不变。

### Testing

受影响时间矩阵34 passed/12.95s，engine34次，禁用执行器/网络/进程/保护访问计数0。日志 assertions-green.log 与 XML 保存在仓库外依赖恢复证据目录；git diff --check通过。新最终HEAD重新认证原五阶段与R1分支/PR双平台，不照抄前一HEAD状态。

### Notes

- tests/research_factory/test_r1_snapshot_integration.py：仅拆开本轮新增的5处复合断言。
- docs/R1_AVAILABLE_AT_NULL_FIX_V1.md：追加实际Sonar提示及验证说明。
- progress.md：仅追加本轮测试质量修正记录。
- 回滚：git revert <本次断言提交SHA>；回滚点725eb559727dcc088e8c06c8134b7288a2e4552b。该回滚不撤销时间校验，只恢复复合断言。
- PR #7仍Draft；旧Windows L1保持OPEN_ROOT_CAUSE_UNCONFIRMED；REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；未merge、未开始R2。

## 2026-09-10 - Task: R1 正式调用方输入输出适配与真实合成链路验证

### What was done

从已核验的 PR #7 合并 main e72fa6ae0ace0dbff6eeac87ae0e09082431d89a 创建独立 worktree/分支 codex/r1-caller-io-parity-v1，保留原工作区改动。接通 canonical caller 的实际文件准备、部署 runner 调用与结果适配；冻结身份、显式日历/预热、时间、双来源 PIT 和结果证据均明确校验。正式 execute 在原治理位置复用同一子路径，本轮未调用完整正式链。real_runtime、其他合同及真实研究仍 NOT_VERIFIED。

### Testing

- 基线真实丢列 red：1 failed，缓存 available_at 经原 _prepare_inputs 被丢弃；原始 red.log 保留。该最初 fixture 在首次设计前重建了合成政策起点；最终 fixture 完整保留原 V2 政策/锁，仅在候选首次审批前声明政策内子窗口，差别未隐藏。
- 中间装配失败独立记录：非法 ExitPredicate 对象、结构语义指纹遗漏、负向合同缺 hypothesis lineage/第二因子 roles，均为实际领域校验提前拒绝，不冒充业务 red；未放宽治理验证。
- 新 venv：Python 3.13.5，Pandas3.0.5/NumPy2.4.6/PyArrow25.0.1/pytest9.1.1；原完整 hash 锁经官方 Simple API 安装，pip check通过。源码 E: exFAT，临时 fixture C: NTFS。
- 原五阶段：隔离先验66 passed；Phase1 235 passed/原12 deselected；Phase2 24 passed；P3-B 35 passed；P3-C 106 passed（311.81s）。既有弃用 warning保留；未新增skip/xfail/选择器排除。
- 源码编译通过；全量收集1090项（既有收集skip保留，收集不计通过）。Node24.15.0前端构建通过，8项前端测试通过；原大chunk警告未扩展处理。
- 完整 R1 209 passed（239.54s）：旧146项在新工作树实跑，新caller矩阵63项；不是抄旧数字。主进程原runner矩阵engine63次、caller22次，独立cold worker另1次。新进程无pytest替身，writes/forbidden=0；所有最终主进程禁用预测/Structural/AI、网络/进程/保护目录探针均0。R1真实合成审批/确认各62次，未冒充零治理动作。
- 合法文件链正向与独立参考输入的信号/排序、订单、成交、lot、退出、metrics/来源精确一致，有BUY/SELL；BASE/10K独立引擎。输入/治理目录内容与mtime不变；默认UNMATERIALIZED、metrics_ref=None，独立显式证据文件与返回内容一致。
- 首次精确比较输入身份35e97ff3aa42435fa16f4b38e29ab444c7e4f9bbef8f6c37d8154260eb328b59、合同ccdf7ffdea31a370197d3a881ae211f2d9e1652f935beeb170875001976b71db，详见suite-7.log。源码原始字节哈希按实际平台记录，不混同LF部署哈希。
- git diff --check通过。两个已合并脚本、engine、原依赖锁与基线无差异；最终按数据/输出边界、范围和禁止路径顺序复核，无新增授权开关或测试替身。既有预算/最终裁决规则不改；仅将真实输入诊断和已写出的provisional引用交接给原流程。
- 提交时双平台分支/PR CI=PENDING；提交后所有run/head/platform证据写仓库外记录和Draft PR，不为状态变化追加提交、不使用PR #7旧CI。

### Notes

- src/chanlun_trader/research_factory/predictive_executor.py：正式复用prepare/invoke/result，保留治理顺序；无证据不猜路径，无执行不交统计裁决。
- src/chanlun_trader/research_factory/caller_inputs.py：新增单缓存因子DAILY/RAW/V2窄准备，显式日历/预热/时间/PIT及输入内容身份。
- src/chanlun_trader/research_factory/data_readiness.py：提取原日线数值/包络/单位校验供双方复用，既有检查/错误码不变。
- tests/research_factory/r1_caller_fixture.py：真实治理服务首次审批前形成自包含合成合同和文件。
- tests/research_factory/test_r1_caller_io_parity.py：63项文件链、独立参考、负向和治理无副作用验证。
- tests/research_factory/r1_caller_cold_worker.py：独立新进程实际准备/engine/适配及禁止写/完整执行探针。
- .github/workflows/r1-source-data-certification.yml：加入本轮测试和原字节cold证据上传，原选择器不变。
- docs/R1_CALLER_INPUT_OUTPUT_PARITY_V1.md：输入输出矩阵、短审计、支持边界、真实验证及失败分层。
- docs/R1_SNAPSHOT_INTEGRATION_V1.md：追加PR #7合并事实和本轮窄适配链接，旧历史保留。
- docs/R1_SOURCE_CLOSURE_AND_DATA_READINESS_V1.md：追加当前部署可加载与其他调用方仍未验证的范围说明。
- CLAUDE.md：追加时间保留、显式日历和真实证据引用约束。
- progress.md：仅追加本轮任务闭环记录。
- 仓库外 E:/llmwiki/r1-caller-io-evidence：保存安装、所有red/装配失败/green、原五阶段/R1日志、XML、冷进程原字节及后续新HEAD CI证据；不推送研究数据或环境。
- 回滚：本轮单提交可执行 `git revert --no-edit codex/r1-caller-io-parity-v1`；回滚点 e72fa6ae0ace0dbff6eeac87ae0e09082431d89a。无需reset或触碰原工作区，历史记录保留并追加撤销说明。
- WINDOWS_L1与WINDOWS_L6继续OPEN_ROOT_CAUSE_UNCONFIRMED；HISTORICAL_PROVENANCE=UNVERIFIED；REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false。完成分支推送后交独立差异复核；不merge/auto-merge、不开始R2。

## 2026-09-10 - Task: 连续交付接续与 R1 批次调用方输入输出适配

### What was done

完整读取用户批准的连续交付文件，核对caller本地/远端HEAD 4f780cf6454c36124f8a9477ca73098551d49f04、实际main e72fa6ae0ace0dbff6eeac87ae0e09082431d89a、12份文件与19份日志哈希。PR #8仍OPEN/Draft/未合并；查询时19项push/PR检查SUCCESS。由caller创建独立总集成分支codex/roadmap-engineering-completion-v1，未改原dirty研究区，未回退main。新增全路线剩余需求及集中真实授权待办矩阵。

批次正式run复用canonical输入/runner/结果适配；冻结日历/时点保留，BASE/10K独立，裁决只引用已落盘provisional。按原合同分别验证registry内容hash或路径/字节sha256，不修改冻结含义。缺输入不伪报零信号。旧policy-pin测试改为临时生成默认政策和锁，消除cwd真实文件依赖。

### Testing

- 新venv Windows Python3.13.5，原requirements-p3b递归哈希锁经官方Simple安装；pip check成功。隔离先验66 passed，network/process/protected探针0。
- 新批次初版4 passed；R1第一轮完整213 passed/251.15s；补原批次文件身份后5 passed/14.71s；最终受影响caller63+批次5+policy6共74 passed/48.13s。不同轮次不合计。真实reader/runner、双独立engine正向及时间/日历/列/文件hash负向通过。
- 扩展回归39 passed/1 failed，失败为旧政策测试读取未交付cwd政策；原日志batch-regression.log保留，不算业务red。临时默认政策fixture修正后6 passed/0.26s，最终74项包含该6项。
- 第一轮R1主进程caller engine22次、原snapshot矩阵63次，cold独立计数保留；批次正向真实运行BASE/10K两次。合成审批/确认与禁止端探针分别记录原日志，不当真实业务动作。未调用完整execute、Trial/PerformanceAccess、真实行情/AI/Paper/broker。
- 源码compile、git diff --check成功；初次全量1094 collected（第五项新增前），非passed。最终固定HEAD整体和双平台认证尚未进行，旧callerCI不认证本次代码。
- 限制：完整RealFactoryRuntime.run及恢复、R2全服务尚未验收；当前不能声明全路线完成。原Windows L1/L6仍OPEN_ROOT_CAUSE_UNCONFIRMED。

### Notes

- src/chanlun_trader/research_factory/real_runtime.py：移除重复准备，复用正式caller、真实诊断及provisional引用；预性能准备失败释放尚活动预留。
- src/chanlun_trader/research_factory/caller_inputs.py：兼容并严格验证已有registry文件身份。
- tests/research_factory/r1_caller_fixture.py：首次审批前可按批次既有格式生成合成registry身份，默认旧fixture不变。
- tests/research_factory/test_r1_batch_caller_inputs.py：新增5项实际批次准备/双组合/错误拒绝验证。
- tests/research/test_predictive_executor_policy_pin.py：政策与锁改为临时现场生成，不读cwd运行工件。
- .github/workflows/r1-source-data-certification.yml：原R1认证增加上述测试，原隔离/选择器/timeout保留。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：接续基线、剩余需求、阶段证据、真实授权待办与限制。
- CLAUDE.md：追加文件与内容registry身份不可混用的经验。
- progress.md：仅追加本轮记录。
- 仓库外E:/llmwiki/roadmap-engineering-evidence：安装、原始失败/成功日志、XML、caller远端证据及后续交接。
- 回滚：在总集成分支执行git revert --no-edit <本轮提交SHA>；回滚点4f780cf6454c36124f8a9477ca73098551d49f04。保留历史日志，不reset、不修改main或原研究目录。下一步自动继续R2合法合成完整服务验证；真实运行仍未授权。

## 2026-09-10 - Task: R2 真实结构结果持久化与完整服务阻塞定位

### What was done

修复真实 Structural producer 与持久化边界对零安全计数的不一致，复用既有精确路径且拒绝非零 Prospective/收益字段。建立现场合成探索驱动，实际规范化 PIT、遍历分片验证下界依据，正式 Structural 得到 PASS，真实治理服务生成预测授权。正式 start 正确拒绝缺失 canonical family，未伪造回执。

另经真实目标创建服务证明输出缺四项冻结执行绑定；提出新版本目标绑定的精确批准范围，未修改身份/治理协议。等待批准期间按用户授权继续独立 R3 工程，R2完整路径不标通过。

### Testing

- r2-blind-boundary-red.log：1 failed/3 passed；修复后针对性4 passed。
- r2-structural-regression.log：88 passed/2 failed/46.98s。两项旧现场测试依赖当前工程根不存在的真实Objective/报告；未读原研究根、未新增skip，不能算通过。network/process/protected=0。
- 五轮探索日志r2-first至r2-fifth及对应root.txt完整保留；每轮针对已定位的fixture/证明缺口修正。最终实际Structural lower64/upper65/minimum30，两个真实build（预核验与服务）；预测、engine、PerformanceAccess均0。start被canonical family缺失阻断。
- r2-objective-binding-gap.log：真实review/confirm创建目标/预算/家族/lineage/receipt后四个绑定字段缺失，旧fixture不能代替该路径认证。R2完整Trial/恢复未验证。
- 原Windows L1/L6继续OPEN；最终全路线和双平台认证尚未完成。

### Notes

- src/chanlun_trader/research_factory/structural_reconciliation.py：复用已有盲性合同并拒绝非零安全计数。
- tests/research_factory/test_r2_structural_result_boundary.py：四项真实序列化边界回归。
- tests/research_factory/r1_caller_fixture.py：新测试可在首次审批前声明完整语义及合成日历，默认不变。
- tests/research_factory/r2_service_worker.py：独立临时根探索驱动，显式标明尚非完整验收。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：实际证据、失败、R2阻塞与具体批准范围。
- CLAUDE.md：追加跨服务盲性与正式Objective fixture边界经验。
- progress.md：追加本轮记录。
- 回滚：执行git revert --no-edit <本轮提交SHA>；回滚点1d461de5c2da74906ecab74e70f0cc6eb41eeeda，不reset/main，不删除历史证据。证据位于仓库外E:/llmwiki/roadmap-engineering-evidence。

## 2026-09-10 - Task: R3 只读批次范围申请与有限循环停止报告

### What was done

在R2身份协议待批准期间继续独立R3工程。复用安全上下文、数据能力、预算和能力注册表，完成批次范围申请服务/API/治理页表单；校验输入、版本、日期、额度、到期和撤回，始终不授予执行权限。表单修改/切目标后旧结果失效，实际调用领域服务。修复有限循环实际停下却报告未停止的问题，不改变执行动作和人工门禁。

### Testing

- r3-request-first.log：17 passed；r3-request-regression.log/XML：105 passed，含原控制平面合法推进/人工等待/权限与隔离。
- r3-loop-limit-red.log：真实Proposal完成后stopped=false复现，1 failed/17 deselected；修复后r3-final-affected.log/XML共106 passed/19.86s，network/process/protected=0。真实下一次loop在人工Freeze前停下，无重复执行。
- npm ci --ignore-scripts使用既有package-lock；原前端8 passed；vue-tsc/Vite最终build成功。原大chunk警告不改阈值，未新增依赖。
- 临时synthetic root、默认READ_ONLY、原import-time isolation下启动一次localhost测试Web，无后台研究恢复。浏览器实际校验成功、编辑后失效、撤回拒绝，截图在任务工具记录；无mock接口。旧fixture缺orchestrator资料的503如实显示/记录，不冒充完整研究运行。测试页关闭，临时服务终止。
- diff --check、单模块compile通过；最终固定HEAD双平台认证未完成。R2完整路径仍待批准；R3其余整合及D1/D2/M1继续实施。

### Notes

- src/chanlun_trader/research_factory/batch_scope_request.py：明确范围契约和只读检查，非授权权威。
- src/chanlun_trader/research_factory/autonomous_control_plane.py：有限循环停止状态与原因纠正。
- src/chanlun_trader/webapp.py：新增只读请求查询/校验入口，现有执行策略中间件不改。
- frontend/src/console/components/BatchScopeRequest.vue：真实API表单、输入更新失效、状态与限制提示。
- frontend/src/console/ResearchConsole.vue：在既有治理页接入申请表。
- tests/research_factory/test_batch_scope_request.py：18项申请/隔离/版本/资源及真实循环停止验证。
- .github/workflows/phase2-control-plane-certification.yml：原矩阵增加新测试，选择器/超时不变。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：追加本阶段实测、操作入口和工程缺项。
- progress.md：追加本轮闭环。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点86c1403，保留历史与外部证据，不修改main。外部原始日志在E:/llmwiki/roadmap-engineering-evidence，浏览器测试输入全部synthetic。

## 2026-09-10 - Task: D1 共享每日语义、资金持仓预览与不可覆盖归档

### What was done

正式runner提取当日信号/PIT输入与执行支持范围校验，共享给每日预览，不另写评分规则。预览复用真实ledger持仓、退出评估器、lot/sizer/fee/slippage，输出买入预览、持有/退出及NOT_READY；副本计算不改原账户。新增合同/数据/账户/代码/时点绑定和不可覆盖归档，变化生成新身份，旧计划可查且标STALE。未授予策略资格或真实执行许可。

### Testing

- d1-preview-first.log：新预览/批次14 passed/28.39s。
- d1-shared-regression.log/XML：原R1快照/caller/批次及每日共160 passed/136.26s；真实runner信号对照、原F01–F04和available_at所处套件保留。
- 增加归档后d1-archive.log：每日12 passed/24.42s；实际ledger Fill产生持仓/T+1、同退出评估器对照、现金改变身份、缺PIT/因子、未来时间/NaT、不可覆盖及损坏归档拒绝。所有轮次network/process/protected=0，重叠测试不合计。
- 本阶段仅每日计算与归档，D1正式资格/API/UI尚未验收，D2/M1未完成。整体固定HEAD/双平台认证仍待最终阶段。不得把研究预览当可用策略或真实观察。

### Notes

- scripts/run_engine_corrected_phase4_v3.py：共享原执行范围校验和当日输入逻辑，runner继续原缓存与执行语义。
- src/chanlun_trader/research_factory/daily_plan.py：账户副本每日预览、资金费用/T+1、版本身份及不可覆盖归档。
- tests/research_factory/test_daily_plan.py：12项真实组件对照、错误输入、身份与归档检查。
- .github/workflows/r1-source-data-certification.yml：原双平台R1矩阵增加每日回归，原skip/超时不改。
- CLAUDE.md：追加共享语义与预览非授权经验。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：本阶段实际完成、证据与必要工程缺项。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点54838ac，保留历史归档和日志，不reset/main。原始证据位于E:/llmwiki/roadmap-engineering-evidence。

## 2026-09-10 - Task: D2 共享引擎逐事件回放、持久恢复与账务对照

### What was done

从现有引擎/runner提取同一事件处理和装配入口，完成真实broker/ledger支持的有界Paper工程回放。逐事件持久历史与源码/合同/输入身份绑定；恢复重放后逐项核对，重复累计请求幂等，写失败必须重新打开。增加页面可读取的历史账务摘要，读取不构造引擎。全程只用隔离合成输入，真实观察天数0。

### Testing

- d2-engine-extraction.log：13 passed/12 failed；12次Git探测被原进程隔离拒绝。修正相关旧合成fixture显式源码身份和禁清单落盘，不放宽隔离。该失败不算业务red，原始拒绝栈保留。
- d2-replay-first.log：5 passed/14.46s；d2-replay-process.log：26 passed/18.06s，含真实子进程落盘后退出73及恢复。
- d2-final-core.log/XML：183 passed/1 failed/151.24s，失败是BUY缩量后FILLED与测试假设不同。d2-partial-diagnostic.log保留1 failed/1 passed；不改既有BUY合同，用真实SELL余量验证部分成交后d2-corrected-core.log/XML：28 passed/22.30s。
- 最终d2-final-affected.log/XML：40 passed，含回放8、每日12及原engine/lookahead20；现金/费用/交易/lot/order/event_hash与完整真实runner一致；历史缺失/损坏、数据变更、写失败与恢复、部分卖出和涨停拒绝。主进程及冷进程实际隔离探针0；中断前计数专门写stdout，非猜测。
- 原始冷进程字节位于process/paper-replay。相同输入目录在冷恢复前后内容不变。最终双平台认证仍待固定HEAD；当前D2公开操作/资格整合未完成，不能宣称D2工程完成。

### Notes

- src/chanlun_trader/engine/engine.py：提取原逐事件处理及结束清算，原run使用同一逻辑。
- scripts/run_engine_corrected_phase4_v3.py：抽出正式engine与回调装配供回放复用。
- src/chanlun_trader/research_factory/paper_replay.py：隔离根逐事件持久回放/恢复、账务哈希及只读历史。
- tests/research_factory/test_paper_replay.py：8项真实回测对照、持久故障、子进程和成交验证。
- tests/research_factory/paper_replay_worker.py：现场合同加载与真实落盘后中断/恢复。
- tests/engine/test_engine_strategy_api.py：合成fixture传UNKNOWN源码身份并禁止清单落盘。
- tests/engine/test_order_broker.py：两处合成fixture采用上述显式身份。
- tests/engine/test_portfolio_exit_semantics_v1.py：合成引擎fixture采用上述显式身份。
- tests/engine/test_universe.py：合成引擎fixture采用上述显式身份。
- tests/lookahead/test_daily_fill_volume.py：未来成交量对照fixture采用上述显式身份。
- tests/lookahead/test_lookahead.py：指数时点对照fixture采用上述显式身份。
- .github/workflows/r1-source-data-certification.yml：原矩阵追加Paper及engine/lookahead测试，不改skip/超时。
- CLAUDE.md：追加逐事件对账和BUY缩量语义经验。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：本阶段实测、原失败与尚未实现项。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点ee58df3，历史模拟归档保留且因源码身份变化拒绝续写，不修改main。证据位于E:/llmwiki/roadmap-engineering-evidence。

## 2026-09-10 - Task: 修复中间CI遗漏的只读路由清单

### What was done

定位54838ac远端Phase1/Phase2失败：新增批次范围GET后精确路由数测试未同步。改为47并显式断言该接口是GET；原25条POST及权限检查不变。

### Testing

- 远端原始失败ci-phase1-54838ac-failure.log、ci-phase2-54838ac-failure.log保存；均为47!=46，无权限探针触发。
- 本地整文件及请求套件42 passed/2 failed，失败为原CI已排除的两项真实现场依赖，未新增排除或读取真实目录；误名r3-route-catalog-green.log实际失败仍原样保留。
- 精确受影响测试及请求18项最终19 passed，见r3-route-catalog-final.log。隔离探针0，最终CI留待新HEAD；原Windows事件状态不变。

### Notes

- tests/research_console/test_research_console_read_boundary_v1.py：同步精确GET数量并断言新路由。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：追加CI失败与验证实情。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点4d94b1b，回滚会恢复已定位的旧计数失败，不修改main。

## 2026-09-10 - Task: M1共享资金与策略归属的组合预览

### What was done

实现显式不可变组合政策接口，复用每日策略语义和同一账本投影；共享现金/费用/容量/换手约束、同股优先级及退出冲突、lot归属和失效原因可追溯。版本或成员不完整时阻断新增买入，保持研究预览不授予策略使用资格。

### Testing

- 首轮真实组件7 passed，m1-preview-first.log。
- 最终组合11、每日计划12、Paper8共31 passed/79.22s，m1-preview-final.log/XML；真实审批与确认各35次，预测/结构/业务AI禁用探针0，网络/进程拒绝/保护目录访问探针0。Paper冷进程证据另存原process目录。
- 当前f2a759e的Phase1/2/P3A/B/C中间CI成功，R1仍运行；不作为本轮或最终HEAD平台认证。
- M1策略使用资格/统一入口仍未完成，两个冻结测试候选不宣称合格策略；全路线维持PARTIAL。

### Notes

- src/chanlun_trader/research_factory/portfolio_plan.py：显式政策及共享账本组合预览。
- tests/research_factory/test_portfolio_plan.py：实际冻结候选、计划与ledger的11项正负向验证。
- tests/research_factory/r1_caller_fixture.py：允许在初始化/首次审批之前指定不同Objective，沿用原fixture流程。
- .github/workflows/r1-source-data-certification.yml：加入组合测试，原超时与选择条件保留。
- CLAUDE.md：记录共享账户投影和资格边界。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：证据与剩余接口范围。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点f2a759e，移除本轮预览不修改main或既有账务。原始证据位于E:/llmwiki/roadmap-engineering-evidence。

## 2026-09-10 - Task: 接通合成计划、组合与Paper操作工作台

### What was done

同一控制台内提供真实服务支持的研究预览/归档/有界回放和账务证据入口。显式服务装配绑定输入根，沿用执行策略与资源锁，领域层核对确认和当前上下文；默认只读不恢复，候选账户和真实资格明确区分。提供新临时根合成演示命令。

### Testing

- 首轮工作台API7 passed；联合原启动隔离、D1/D2/M1共106 passed/107.35s，workbench-final.log/XML。网络/进程拒绝/保护根探针0；原业务执行禁用计数0。Starlette弃用警告保留。
- 补齐已有输出树链接检查后，工作台最终受影响7 passed/18.45s，workbench-output-check.log；未扩大隔离白名单。
- frontend既有8项测试通过，workbench-frontend-tests.log；最终build成功，workbench-frontend-fixed.log，大chunk警告未调整阈值。
- 浏览器真实预览、归档、9事件2成交、44事件6成交；现金333175.17、费用830.83，重载仍44且推进禁用。第二候选NO_SESSION，资格/真实观察天数0。完成目标控件曾显示45，已修为44并重载确认。临时服务停止、8857无监听，原HTTP日志保留；没有凭空填写UI进程退出探针。
- 2cd521b中间六套CI成功；本轮及最终固定HEAD仍需认证。正式准入与完整服务缺项见矩阵，未宣布工程全部完成。

### Notes

- src/chanlun_trader/research_factory/engineering_workbench.py：实际预览/归档/逐事件服务与确认边界。
- src/chanlun_trader/webapp.py：显式装配与只读/操作API，保留原middleware语义。
- frontend/src/console/components/EngineeringWorkbench.vue：真实API表单、资金/成交/证据、确认失效与只读状态。
- frontend/src/console/ResearchConsole.vue：统一导航入口，工程页不加载无关默认Objective。
- tests/research_factory/test_engineering_workbench.py：实际服务与API7项，含只读、确认、上下文、损坏和新应用继续。
- tests/research_factory/workbench_demo.py：新合成根的显式本机演示启动。
- .github/workflows/r1-source-data-certification.yml：加入工作台测试，原边界/超时不扩大。
- CLAUDE.md：记录显式输入与独立账户边界。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：实际能力、操作说明与必要缺项。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点2cd521b。先Ctrl+C停止临时Web服务，保留独立合成归档，不修改main或真实数据。外部证据E:/llmwiki/roadmap-engineering-evidence。

## 2026-09-10 - Task: 新进程凭显式配置重建工程工作台

### What was done

持久化只绑定输入的工作台装配描述，新进程重新通过正式caller读取原冻结registry/政策/文件并验证身份；公共API确认后从已有事件继续。支持明确旧合成根的resume，配置不携带批准，不自动迁移或删除历史。

### Testing

- 首轮4 passed；最终配置5与工作台7共12 passed/31.11s，workbench-restart-first.log、workbench-restart-final.log/XML。
- 真子进程仅接配置路径，公共API从9事件继续至完成，输入原字节不变；process/workbench-restart保存原stdout/stderr，隔离三探针0，真实观察0。超时沿用60秒，未扩大白名单。
- 输入变更、重算hash后的路径越界/NaN、未重算hash的配置损坏均拒绝；load无落盘。原Starlette警告保留。

### Notes

- src/chanlun_trader/research_factory/engineering_workspace.py：显式装配描述保存与正式caller重建。
- tests/research_factory/test_engineering_workspace.py：只读重建、坏输入/配置与真实子进程5项验证。
- tests/research_factory/workbench_restart_worker.py：新进程通过实际Web API继续回放。
- tests/research_factory/workbench_demo.py：新建保存配置与显式resume，不自动恢复执行。
- .github/workflows/r1-source-data-certification.yml：加入工作台重建测试。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：更新能力和重启命令，旧阶段结论保留为历史。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点8e0c72d。回滚后仅失去此装配入口，原输出和配置留存，不修改main或真实根。

## 2026-09-10 - Task: 合成工作区备份与新根恢复验证

### What was done

实现显式配置输入/输出树的受控备份、逐文件清单及新目录恢复CLI；复用工作台锁，拒绝覆盖/越界/损坏/超限，恢复后正式caller重新核验但不启动事件。补充操作与保留失败证据说明。

### Testing

- 首轮3 passed；备份/重建/工作台联合15 passed/34.31s，workbench-backup-first.log、workbench-backup-final.log/XML；清单字节计入上限后的受影响3 passed，workbench-backup-limit.log。
- 真实CLI备份90个合成文件到synthetic-workbench-backup.zip，恢复至新根返回RESTORED_WITHOUT_EXECUTION；setup/backup/restore各进程日志与根指针保留，隔离探针均0。
- 恢复账务与原9事件一致，新根续至10不改变原根；损坏内容、清单入口绝对路径、ZIP越界、已有恢复目录和超限拒绝。无真实数据备份/恢复，无新增skip/超时。

### Notes

- src/chanlun_trader/research_factory/engineering_backup.py：限定树的备份/恢复与命令行。
- tests/research_factory/test_engineering_backup.py：恢复后实际续跑及路径/完整性/资源约束3项。
- .github/workflows/r1-source-data-certification.yml：加入备份测试。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：实际证据及备份恢复说明。
- CLAUDE.md：记录清单入口和字节边界。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点de3199a，保留外部ZIP和新旧合成根，不删除或覆盖任何历史目录，不修改main。

## 2026-09-10 - Task: 以真实执行证据替换微观时序常量通过

### What was done

按既有冻结政策核对实际signals/orders/trades/lots/calendar和数据可得时点，复用既有涨跌停规则。单候选与批次正式裁决引用实际核验结果，保留原模型、阈值与预算语义。

### Testing

- 提取原常量逻辑后，真实runner结果副本的同bar/NaT/T+1/未来因子/停牌注入得到5 failed/1 passed；这是gate组件red，不冒充完整执行路径red。execution-evidence-red.log原样保留。
- 首轮核验6 passed；增加非开盘时点后，相关微观/batch/D1/D2/工作台最终39 passed/86.08s，execution-evidence-final.log/XML，隔离三探针0；警告保留。
- 完整canonical与批次服务仍待后续；其他hard gate不因本项通过而认证。

### Notes

- src/chanlun_trader/research_factory/execution_evidence.py：真实微观执行证据核验。
- src/chanlun_trader/research_factory/predictive_executor.py：正式gate使用核验结果。
- src/chanlun_trader/research_factory/real_runtime.py：批次gate不再按候选类型自动通过。
- tests/research_factory/test_execution_evidence.py：实际runner正向与6种证据异常。
- .github/workflows/r1-source-data-certification.yml：加入执行证据验证。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：原缺陷、测试范围及剩余hard gate。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点01addc3，会恢复已知常量gate缺陷，禁止据此启用真实研究。main及真实根不变。

## 2026-09-10 - Task: 核对真实预算预登记并接入正式裁决

### What was done

以实际Trial首事件、身份与预算状态替换预算gate常量；批次补传已有预留身份字段，事前核验失败不误记性能消费。保持原预算上限、扣账和恢复语义，不修改历史身份。

### Testing

- 首轮6 passed；扩大测试38 passed/22 failed，失败全部为旧启动夹具缺失同一未交付合同，原始budget-registration-final.log/XML保留。
- 可独立关联37 passed/27.87s；补实际batch引用与错绑负向后12 passed/10.43s，budget-registration-affected及binding日志/XML。隔离三探针0，无新增skip/超时；完整服务尚未认证。
- git diff --check通过（原CRLF提示保留）。

### Notes

- src/chanlun_trader/research_factory/trial_adapter.py：只读首个预登记事件核验。
- src/chanlun_trader/research_factory/execution_evidence.py：实际预算状态与身份核验。
- src/chanlun_trader/research_factory/predictive_executor.py：正常/恢复执行与最终gate引用真实证据。
- src/chanlun_trader/research_factory/real_runtime.py：传入原预留身份，事前核验与失败释放。
- tests/research_factory/test_budget_registration_evidence.py：7项真实facade与负向证据测试。
- .github/workflows/r1-source-data-certification.yml：加入预算证据测试。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：记录结果、失败入口及消费后引用限制。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点e6adc07，会恢复预算gate常量缺陷；保留全部外部日志，不改main或真实根。

## 2026-09-10 - Task: 批次最终裁决绑定实际事前新颖性结果

### What was done

最终相似性gate引用原实际新颖性决策及比较集身份，删除批次常量通过。记录策略使用资格新人工入口所需的具体批准范围，未实施该权限协议。

### Testing

- 批次及既有新颖性/编排36 passed/20.73s，batch-novelty-evidence.log/XML，隔离三探针0；无新增skip/超时。
- canonical相似性证据与完整服务仍未认证。

### Notes

- src/chanlun_trader/research_factory/real_runtime.py：直接传递事前新颖性决策。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：结果与新增资格批准待办。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点3a49d06，保留外部证据，不改main或真实根。

## 2026-09-10 - Task: 策略注册事实接入工作台与失效检查

### What was done

只读显示实际研究状态和未准入原因；退役、证据失效与合同冲突剔除预览并阻止继续回放，绑定状态变化到上下文和计划身份。记录用户已批准仅合成测试资格服务，后续继续正向实现。

### Testing

- 首轮3 passed/1 failed为测试请求非法DRAFT→RETIRED；改用既有合法路径后联合19 passed/47.49s，strategy-admission-first/final.log/XML，隔离三探针0。
- 前端8 passed、build成功；原chunk/Starlette警告保留；浏览器待完整资格功能一起验证。

### Notes

- src/chanlun_trader/research_factory/strategy_admission.py：只读canonical registry投影。
- src/chanlun_trader/research_factory/engineering_workbench.py：状态、计划身份和失效阻断。
- frontend/src/console/components/EngineeringWorkbench.vue：当前研究状态与原因。
- tests/research_factory/test_strategy_admission.py：实际登记/退役/失效/错版4项。
- .github/workflows/r1-source-data-certification.yml：纳入只读准入测试。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：证据与具体批准更新。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点962fe78，保留外部日志与registry历史，不改main。

## 2026-09-10 - Task: 实现用户明确批准的仅合成测试使用资格

### What was done

接通实际请求、人工确认、用途/有效期核验、撤销与持久证据；组合过滤合格测试来源，发布和回放重新核验输入与资格。完成界面两步确认及撤销禁用，真实策略资格仍0，不修改既有Trial/预算/CP权限。

### Testing

- 首轮8、扩展99、最终联合101 passed/95.79s；最后到期边界受影响21 passed/57.52s，synthetic-usage-complete/expiry.log/XML。禁止执行探针0，合法旧模板5，旧治理请求34/确认37；进程隔离三项0。新资格有实际持久请求/确认/撤销证据，未mock权限。
- 前端最终8 passed/build成功；保留原警告。浏览器双资格组合、实际2笔成交、撤销409、最终新根服务停止/新进程配置恢复9→10事件并撤销禁用全部验证；原始UI日志及根指针保存，未删除开发历史。
- 新增输入修改、资格用途/过期、校验途中到期、跨根复制、无请求确认、损坏确认、成员移除、幂等及真实两候选资金竞争验证。并未启动真实数据Trial或Paper。

### Notes

- src/chanlun_trader/research_factory/synthetic_usage.py：实际隔离测试资格请求/确认/撤销服务与只读检查。
- src/chanlun_trader/research_factory/engineering_workbench.py：资格过滤、发布复核、回放门禁及执行证据。
- src/chanlun_trader/webapp.py：受原本机/策略边界保护的公共资格入口。
- frontend/src/console/components/EngineeringWorkbench.vue：有效期、两步确认、撤销和失效说明。
- tests/research_factory/test_synthetic_usage.py：14项实际服务与安全/身份/时间边界。
- .github/workflows/r1-source-data-certification.yml：加入资格回归。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：精确批准、测试、操作与限制。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>，回滚点ab41aa4；回滚后停止合成服务，不用旧工程预览模式继续受资格约束的回放。保留所有资格/回放/外部证据，不迁移真实registry、不改main。

## 2026-09-10 - Task: 修复资格历史丢失后的错误回退

### What was done

结构化自检发现并修复资格模式依赖当前记录数的问题。首次实际请求保存持续收紧标记，历史丢失不恢复无资格回放；保留旧记录和失败证据。

### Testing

- 实际服务登记/确认后移走历史目录，原实现第1事件未拒绝，red 1 failed/4.62s；synthetic-usage-history-red.log保留。
- 修复后资格15+备份3共18 passed/50.68s，synthetic-usage-history-green.log/XML；隔离三探针0，无新增skip、超时或权限白名单。

### Notes

- src/chanlun_trader/research_factory/synthetic_usage.py：持续收紧标记及只读验证。
- tests/research_factory/test_synthetic_usage.py：实际目录丢失场景，使用同根保留目录而非删除。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：自检缺陷、证据和旧开发根限制。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点f2541c0，会恢复已知回退缺陷，须停止合成服务并保留历史。main及真实根不变。

## 2026-09-10 - Task: 提供显式配置的源码工作台启动入口

### What was done

从正式源码模块提供只读检查和本机工作台服务，支持不同cwd和既有配置恢复；不依赖演示夹具生成，不自动推进事件。

### Testing

- 冷重建及实际子进程检查6 passed/19.91s，workbench-package-cli.log/XML，进程/网络/受保护访问探针0。
- 实际源码服务浏览器只读显示10/44事件、2成交，写入控件禁用，真实资格/观察天数0；workbench-package-server.log。已停止并核对端口关闭。

### Notes

- src/chanlun_trader/research_factory/engineering_workspace.py：inspect/serve、显式端口、固定本机与默认只读入口。
- tests/research_factory/test_engineering_workspace.py：不同cwd实际子进程无写检查。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：源码部署命令、证据和wheel范围限制。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点f3ab4fd。停止合成服务，保留配置和全部证据，不改main或真实根。

## 2026-09-10 - Task: 固化全路线集中审计范围及最终验证入口

### What was done

整理当前需求与实际调用链、十五项跨系统场景、部署/恢复和必要阻断；将早期阶段快照与当前已实现范围分开。补齐结构持久边界测试到现有R1双平台工作流。

### Testing

- 文档逐项对照已提交代码、实际原始日志与附件第13—17节；不把片段通过写为完整R2闭环。
- 最终固定本提交后的HEAD执行原阶段与全部新增套件，结果写外部final清单及PR，不再以回填结果改变HEAD。
- 本轮git diff --check；工作流仅增加已通过的4项边界用例，未调整原选择器、skip、timeout或白名单。

### Notes

- docs/ROADMAP_CONTINUOUS_DELIVERY_AUDIT_V1.md：当前矩阵、实际调用图、跨系统判定、集中待办及部署/证据导航。
- .github/workflows/r1-source-data-certification.yml：加入既有新增结构持久边界回归。
- progress.md：追加本轮记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点1223664，仅去除本轮文档/测试入口，不删除原始证据或改变main。

## 2026-09-10 - Task: 按明确批准实施隔离 synthetic 事前新颖性比较集绑定

### What was done

记录本次用户“选择 1”的批准：仅限隔离 synthetic 新流程的来源解析、快照、启动预览与实际人工测试确认、性能访问前复核及原 CandidateNoveltyGateV2。Objective execution_binding 继续独立待批；不扩展预算、统计、CP、真实研究、Trial、Paper 或订单权限。

实现实际 registry 全成员收集、设计 allowlist、精确自身排除、冲突阻断、完整来源映射、版本链/当前版本/工作区身份、不可覆盖预览与测试确认。新版启动合同携带绑定，canonical 短准入边界复用原资源锁和 Gate；旧流程不迁移且不冒称新绑定。补充正式 CLI 和整体认证入口。

### Testing

- novelty-cli.log/XML：24 passed，实际服务确认、空/非空、换名重复、参数邻居、来源异常/变更/缩小/丢失、跨根、CLI、其他门禁、兼容通过；执行/网络/受保护访问探针均 0。真实子进程锁内 writer 拒绝、锁外成功、重启确认复核保留原始 stdout/stderr。
- novelty-scope-head.log/XML：22 passed；早期两次新增夹具断言失败完整保留，不作为旧业务缺陷 red。
- 顺序自检：确认后来源变化不刷新授权；损坏当前范围不退旧版本；原确认不替代启动 intent；新 intent 内容 hash 复核且不迁移旧 intent；长期计算在共享资源锁外。I/O 拒绝是异常注入，不称 OS ACL 验收。
- git diff --check 通过。完整固定 HEAD 回归/双平台结果将写仓库外 final-novelty，原 d821d0a final 证据不覆盖。完整 R2 服务启动与恢复仍未认证。

### Notes

- src/chanlun_trader/research_factory/synthetic_novelty.py：实际来源范围、盲化快照、确认与共享锁复核。
- src/chanlun_trader/research_factory/synthetic_novelty_start.py：版本化预览/实际 intent 绑定，旧 intent 拒绝。
- src/chanlun_trader/research_factory/synthetic_novelty_cli.py：正式声明、预览、测试确认、历史及只读启动预览命令。
- src/chanlun_trader/research_factory/predictive_executor.py：新绑定复核与原性能准入共用短临界区、真实 Gate 证据。
- tests/research_factory/test_synthetic_novelty.py：实际服务与原算法正负向、兼容及身份/范围测试。
- tests/research_factory/novelty_worker.py：真实进程重启和 registry 写锁竞争。
- .github/workflows/r1-source-data-certification.yml：原双平台选择器加入新测试，不改 skip/超时/白名单。
- docs/SYNTHETIC_NOVELTY_BINDING_V1.md：授权、来源信任边界、部署、需求/证据/限制。
- docs/ROADMAP_CONTINUOUS_DELIVERY_AUDIT_V1.md：新版本增量与旧版本缺口区分，保留整体 PARTIAL。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：追加批准及最新范围索引。
- progress.md：本轮实际实施与验证记录。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点 d821d0ab99a181329949c92383a2ab1495009c15。保留所有历史合成证据；不改 main，不移动真实目录。

## 2026-09-10 - Task: 保留新 Windows 拒绝事件并补 P3B 失败流诊断

### What was done

固定 a64c49a 本地 835 项通过，但 R1 PR Windows 在旧 P3C 并发用例出现 cmd.exe 拒绝/exit79。保存失败，新增 OPEN_ROOT_CAUSE_UNCONFIRMED；不关闭原 L1/L6。补齐原 P3B finish 断言前 stderr/stdout 文本留存，标明非原字节，保留超时、退出断言和隔离规则。

### Testing

- novelty-p3b-diagnostic.log/XML：4 passed、14 定向选择器 deselected。真实 P3C/P3B 合成进程拒绝都仍 exit79，完整脱敏诊断留存；两个真实并发场景通过。此通过不是旧 Windows 根因修复证据。
- a64c49a 原本地完整 835 passed、1224 collected、389 未执行精确清单保留；Linux R1 348 passed，其中新颖性24。失败 run 34461942454 不删除、不重跑。
- 新固定 HEAD 后完整回归/双平台证据将单列新目录，不覆盖 a64c49a 失败版本；git diff --check 通过。

### Notes

- tests/research_factory/test_restart_recovery_v1.py：原文本 finish 在失败断言前留存诊断，不改变业务调用与退出判断。
- tests/research_factory/test_process_diagnostics_v1.py：同一真实拒绝场景覆盖原字节/P3B 文本两条路径。
- docs/R1_WINDOWS_PROCESS_DIAGNOSTICS_V1.md：新增 Windows OPEN 事件及文本诊断缺口，保留原事件。
- progress.md：追加本轮证据与限制。
- 回滚：git revert --no-edit <本轮提交SHA>；回滚点 a64c49abad5b180334b765c21e3d580f22b6b81d，不删除失败日志、不改main。

## 2026-09-10 - Task: 接续 A/B 限定批准并实现新 Objective 执行绑定
### What was done
- 分别登记 A Objective execution_binding 与 B synthetic 有界批次为 IMPLEMENTING，保留此前新颖性和测试使用资格批准；旧集中审计包保持不变。
- 新 v2 创建入口在审核预览前验证冻结政策/锁、窗口、实际因子及事件 registry，绑定工作区及来源字节身份，确认复核后复用原 Objective/预算/家族/lineage/回执创建事务。
- 新目标直接进入真实设计、批准、冻结、物化服务，无事后补字段、补家族或补成功回执；修复物化层对正式禁止标记 recommendation=DISABLED 的误拒绝，其他推荐/绩效内容仍拒绝。
### Testing
- objective-binding-stage-final.log/XML：83 passed / 48.69s；含新协议20项、原创建/物化/设计和新颖性回归。原始日志位于 E:/llmwiki/roadmap-engineering-evidence；网络/受保护访问/未授权进程探针均0。
- 实际子进程：持有政策共享锁时确认退出23；Objective 写入后进程退出73；新进程恢复并重放，预算文件字节不变。原始 stdout/stderr 在 objective-binding-stage-process/objective-binding。
- objective-materialization-red.log：真实组合1 failed，精确定位 objective.risk_constraints.recommendation；修复后正向通过，ENABLED 和嵌套 performance 仍拒绝。新协议开发早期2项测试字段/检查点误用及能力 fixture 缺口的失败日志保留，不归类业务 red。
- git diff --check 通过。顺序内部审查覆盖版本混用、来源 freshness、路径/域、共享锁、恢复和旧预算不变；不是独立集中审计或双平台完成。
### Notes
- .github/workflows/r1-source-data-certification.yml：加入新协议验收文件，未扩大 skip/超时/隔离白名单。
- CLAUDE.md：记录正式禁止标记与结果盲化的区别。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：分别登记四项批准及当前实施状态。
- docs/OBJECTIVE_EXECUTION_BINDING_V2.md：新合同、API、源证据、并发边界、兼容及限制。
- src/chanlun_trader/research_factory/objective_execution_binding.py：新版本服务及正式事务复用。
- src/chanlun_trader/research_factory/research_proposal_governance.py：显式 preview/receipt 版本及恢复版本校验；v1 默认值不变。
- src/chanlun_trader/research_factory/candidate_executable_materialization.py：仅对精确禁止值使用临时盲化投影，原文件/来源 hash 不变。
- src/chanlun_trader/webapp.py：独立 v2 review/preview/confirm/recover 入口。
- tests/research_factory/test_objective_execution_binding.py：真实服务正负向、兼容、正式创建至物化与进程边界。
- tests/research_factory/objective_binding_worker.py：独立进程实际确认、退出、恢复和重放驱动。
- progress.md：本轮实现、验证与回滚记录。
- 回滚点 e288746490912ae979dd3643f36eaee063335720；执行 git revert 本模块提交，停用 v2 入口，保留新历史工件并由旧入口拒绝写入；不 reset/revert main。
- 完整 R2、B 实际批次及最终固定 HEAD 整体/双平台验收仍需继续。工程总体 PARTIAL；真实数据 NOT_VERIFIED，READY_FOR_REAL_TRIAL=false，R1_FULLY_CLOSED=false；所有既有 OPEN 事件保留。

## 2026-09-10 - Task: 完成正式服务 R2 合成链及同 Trial 中断恢复
### What was done
- 新建合成目标从实际 v2 创建事务开始，直接完成设计/批准/冻结/物化、实际 PIT 规范化与 Structural、实际新颖性及预测确认、Trial/预算/家族、两个 engine、统计裁决、registry 和盲化失败回流。
- 复用 R1 测试的数据/候选生成段，明确拆出不写 Objective/预算/家族的入口；新目标及创建工件在物化后字节不变。
- 修复实际 PIT reader 对较长源历史的误拒绝：只投影明确执行日历，窗口内缺任一路仍拒绝。
- 正常完成的新进程确认/恢复重放不重复 engine 或性能准入；另一根实际性能准入后进程退出73，经原协议结算及真实恢复预览/确认完成同一 Trial，消费仍1、预留0。
### Testing
- r2-formal-stage.log/XML：148 passed / 149.82s，包含2条独立进程完整服务组合、2项 PIT 窗口正负向及原 F01–F04/available_at/caller 回归，三项隔离探针0。
- r2-pit-window-red.log：1 failed/1 passed；r2-pit-window-green.log/XML：146 passed。原始真实组合第二轮同样复现窗口误拒绝，未运行 engine，但性能准入已记账；该失败根与日志保留。
- 每条完整正常链实际 Structural build2、engine2、predictive_execute1、performance_access1、外部AI0；最终BLOCKED（RAW_BOOTSTRAP_NOT_SUPPORTED），registry正确VALIDATION_BLOCKED并产生失败条目，不要求RESEARCH_PASSED。
- 子进程原始流位于 E:/llmwiki/roadmap-engineering-evidence/r2-formal-stage-process/r2-formal，正常与中断测试分目录，单次60秒边界未扩张。恢复完成后仍同一Trial、原预算消费1，正常重放账本字节不变。
- 首轮缺universe policy的合成输入失败、第二轮PIT失败、第三轮首次完整完成、后续自动化验收与中断探索均保留原始根/日志，不合并计数，不把 fixture 缺输入当作业务red。git diff --check通过；内部顺序检查通过，不是独立审计完成。
### Notes
- .github/workflows/r1-source-data-certification.yml：加入PIT窗口及正式R2组合测试。
- CLAUDE.md：记录较长PIT历史与执行窗口投影规则。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：更新A/R2当前本地工程验收状态，B继续IMPLEMENTING。
- docs/R2_FORMAL_SYNTHETIC_SERVICES.md：完整服务、恢复、计数、失败证据与限制矩阵。
- scripts/run_automated_strategy_validation_v1_rerun_v2.py：窗口外状态不参与窗口覆盖判定。
- tests/research_factory/r1_caller_fixture.py：提取可复用因子和正式已创建目标的候选/行情生成段，旧fixture默认行为保持。
- tests/research_factory/r2_formal_fixture.py：实际创建目标至真实物化；创建后不补字段/预算/家族。
- tests/research_factory/r2_service_worker.py：从旧探索驱动升级正式新流程，增加真实新颖性、账本/registry/失败断言与退出点。
- tests/research_factory/r2_recovery_worker.py：真实新进程重放及实际人工确认恢复驱动。
- tests/research_factory/test_pit_window_projection.py：覆盖窗口内完整/缺失及窗口外拒绝。
- tests/research_factory/test_r2_formal_services.py：正常完整链和性能后退出/恢复两个真实进程验收。
- progress.md：本轮证据和回滚记录。
- 回滚：git revert 本阶段提交，恢复点991281a；已创建目标与Trial历史保留、不返还消费、不删失败工件，不merge/reset main。
- 总工程仍PARTIAL。B实际有界批次、最终固定HEAD回归/双平台/新版审计包待完成。真实数据NOT_VERIFIED、READY_FOR_REAL_TRIAL=false、R1_FULLY_CLOSED=false，真实策略0、真实观察0，旧OPEN事件不关闭。

## 2026-09-10 - Task: B 合成批次实际资源执行组件
### What was done
- 实现 Windows 挂起启动、同一 Job 总内存、父进程退出清理和 Linux 地址空间/时间/父进程生命期限制；领域组件必须在资源握手后导入。
- Windows venv 启动器及解释器共享已声明总内存，进程上限2；不把配置打印当作限制生效。
### Testing
- batch-resources-suspended.log/XML：3 passed；正常、实际内存分配失败、实际超时终止均有独立原始 stdout/stderr。首轮失败和中间通过日志全部保留。
- 尚未完成最终固定 HEAD 双平台，批次调度、撤销竞争和恢复验收继续；本提交不标 B 完成。
### Notes
- src/chanlun_trader/synthetic_batch_resources.py：实际 OS 资源及进程生命期约束。
- tests/research_factory/batch_resource_worker.py：资源握手后执行正常、内存和超时动作。
- tests/research_factory/test_synthetic_batch_resources.py：真实子进程正负向断言及原始流保存。
- docs/SYNTHETIC_BATCH_RESOURCES_V1.md：执行指标、适用平台和当前证据限制。
- progress.md：本轮结果、失败证据和回滚记录。
- 回滚：git revert 本资源模块提交；恢复点90f5f0c，停用新批次入口并保留合成历史和失败工件，不重置 main。

## 2026-09-10 - Task: B 明确候选合成批次授权与实际有界服务组合
### What was done
- 形成独立版本的预览、实际测试人工确认、委托派生、单进程实际调度、暂停/停止/到期/撤销及审计恢复。每个明确候选保留自己的正式 Objective、原预算和统计家族；没有新建可重置消费的预算账本。
- 启动与首次性能访问核验父批准、候选、完整来源、原创建事务和不可变家族；批次锁与原共享来源锁保持至内存输入快照完成，两个 engine 在锁外运行。
- 实際委托标记 BATCH_DELEGATED，旧启动入口拒绝消费它；删除 metadata 不能绕过 canonical 意图，委托不能改成未授权收尾类型或调用重试入口。旧 CP 预测禁令不变。
- 实际 worker 性能准入后退出按原协议消费；控制者退出恢复只结算不自动重跑。撤销事件先于 head 落盘时只能前滚该完整事件，不回退旧 ACTIVE。
### Testing
- batch-admission-review.log/XML：27 passed，包括正式创建工件、单/多候选完整链、原额度、实际进程退出/恢复、来源变化和性能前撤销；受拒绝的竞争场景 engine0/performance0。
- batch-snapshot-boundary.log/XML：5 passed；源锁在真实 caller 快照读取期间拒绝并发替换，随后实际 engine2/performance1；原两个 R2 完整服务/恢复场景通过。
- batch-quota-final.log/XML：2 passed；Trial数不足拒绝，原实际消费后不能通过另一批次重置预算。batch-root-check：2 passed，相对输入根拒绝、真实合同确认通过。
- batch-recovery-final：4 passed；实际控制者退出73、新进程结算、到期、8MiB阻断，以及撤销事件落盘/head更新前退出73。batch-core-final：24 passed，含实际OS资源与原完整R2组合。
- batch-stage：77 passed/22 failed；旧启动测试依赖缺失历史合同，在fixture第40行失败，未进入启动服务。测试源码与e288746相同，Git blob均9459bbc20ec42c971df634250375739fef8216b3。没有读取真实目录补合同，没有改变旧测试、skip或隔离名单；失败保持独立，不以其他集合通过覆盖。
- batch-synthetic-stage：78 passed/1 failed；96MiB负向假设错误，实际Structural成功。改用明确8MiB负向输入后阻断；原96MiB成功与失败断言证据保留，不算业务缺陷red，不扩大资源或超时。
- 新测试原始流按各自r3-contract临时根分目录保存，实际父子进程、退出码、预算和域状态均留证；新增CI入口及原始流上传路径。git diff --check通过；本轮顺序内部自检不冒充独立集中审计或最终双平台。
### Notes
- .github/workflows/r1-source-data-certification.yml：加入批次资源/合同/恢复/竞争测试，并上传正式A/R2/B原始进程证据。
- CLAUDE.md：记录Windows启动器和资源握手，以及历史委托结算范围。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：统一B当前IMPLEMENTING状态，启动时矩阵明确为历史。
- docs/SYNTHETIC_BATCH_AUTHORIZATION_V1.md：协议职责、实现边界、正负向和失败证据、剩余必要工程。
- src/chanlun_trader/research_factory/synthetic_batch.py：父批准、实际边界、原领域结算、重启和事件恢复。
- src/chanlun_trader/research_factory/synthetic_batch_delegation.py：明确版本的领域授权解析、重试拒绝及性能准入。
- src/chanlun_trader/research_factory/predictive_trial_start.py：旧入口拒绝批次委托授权记录。
- src/chanlun_trader/research_factory/predictive_executor.py：性能准入与输入快照在原共享来源边界内完成。
- src/chanlun_trader/synthetic_batch_worker.py：实际受限进程调用既有Structural/预测领域服务。
- tests/research_factory/r1_caller_fixture.py：在首次设计前支持明确持有周期，默认不变。
- tests/research_factory/r2_formal_fixture.py：第二个已知候选仍经过正式v2创建，原目标和额度不改。
- tests/research_factory/r2_service_worker.py：生成单/多候选正式测试前置，未预填PASS或授权回执。
- tests/research_factory/batch_interruption_worker.py：真实engine边界退出注入。
- tests/research_factory/batch_controller_worker.py：真实控制者退出、恢复和head写入中断。
- tests/research_factory/batch_boundary_race_worker.py：暂停真实边界供另一操作方并发变更，保存实际计数。
- tests/research_factory/test_synthetic_batch_contract.py：正式合同、正负向、单/多候选、原预算与兼容。
- tests/research_factory/test_synthetic_batch_recovery.py：控制者重启、到期、实际资源不足与撤销事件恢复。
- tests/research_factory/test_synthetic_batch_races.py：性能前撤销/来源变化及实际快照共享锁。
- progress.md：本轮结果、差异与回滚记录。
- 回滚：git revert 本模块提交，停用B新入口并保留全部新合成历史/消费；恢复点991adc3。不reset/merge main，不返还已消费预算。
- 当前总工程仍PARTIAL；B正式操作入口/统一界面、固定新HEAD全路线回归、双平台和新版集中审计包继续。真实数据NOT_VERIFIED、READY_FOR_REAL_TRIAL=false、R1_FULLY_CLOSED=false；既有OPEN事件不关闭。

## 2026-09-10 - Task: 批次合成夹具的临时路径一致性
### What was done
- 创建任何正式目标前规范化测试临时根，父进程与真实服务子进程使用同一根身份；保存原临时路径和规范路径诊断。未改变服务来源校验。
### Testing
- 320e980 的 PR R1 run 34490995583：Ubuntu通过；Windows 376 passed、9 failed、20 errors，新增批次夹具在NOVELTY_SOURCE_MISSING_OR_LINKED处阻断。原始失败日志及全部附件保存到外部证据目录 batch-ci-pr-34490995583；push run34490990216为cancelled，不记通过。
- batch-temp-alias-final.log/XML：1 passed，23 deselected（仅定向验证，不代表全套）；真实正式创建、来源确认及批次预览经过非规范临时别名测试，进程探针均0。新的Windows CI尚待验证，不据本地结果关闭历史OPEN事件。
- batch-temp-alias.log为首次验证失败原件：测试断言字段名写错，且该命令误用保护环境变量名，sitecustomize报告KeyError，不能作为隔离认证证据。修正命令与断言后使用独立final日志，未覆盖原件；没有读取受保护研究数据。
### Notes
- tests/research_factory/test_synthetic_batch_contract.py：规范化新夹具根、记录路径、真实服务别名回归。
- docs/SYNTHETIC_BATCH_AUTHORIZATION_V1.md：记录平台阶段差异及验证范围。
- CLAUDE.md：记录父子进程临时根身份一致性。
- progress.md：追加本轮诊断和验证原件说明。
- 回滚：git revert 本轮提交；检查点320e980，不回退main，不改历史批准或账本。

## 2026-09-10 - Task: R3正式操作入口与统一工作台
### What was done
- 接入真实服务批次清单、预览、确认、连续执行、暂停/停止/撤销与只结算恢复；默认只读查询不改变历史。
- 统一页面展示完整来源和明确额度，将批次完成与策略研究结论分开；实际多个候选由一次合成测试父批准连续执行。
- 逐项审阅HTTP本机/策略边界、GET不变性、父批准状态、异步页面更新和历史选择；按仓库约定串行内审，不宣称独立外审完成。
### Testing
- batch-web-final.log/XML：4 passed，1既有Starlette弃用警告，进程探针0；含真实worker执行、未确认拒绝、只读不变、非本机写拒绝及未来/到期撤销。
- batch-ui-build.log：vue-tsc与Vite通过，原bundle体积警告保留。
- 真实浏览器统一页面：批次625e3ba4e2244d4abdbb556385a7d868，经实际预览/合成测试确认后两个候选四个动作完成，全部BATCH_DELEGATED；原预算耗尽后清单阻断，工作台VALIDATION_BLOCKED、可用策略0、真实观察0，浏览器error/warn为空。原HTTP JSON和服务器日志保存到外部证据目录batch-ui-*。
- 首次PowerShell读取localhost未禁用环境代理，返回502；改用-NoProxy读取明确本机API后成功，未修改服务网络或权限。浏览器导出不支持，未声称生成页面导出文件。
### Notes
- .github/workflows/r1-source-data-certification.yml：加入正式批次API测试。
- src/chanlun_trader/research_factory/synthetic_batch.py：只读历史和预览、受限claim、未来/到期的暂停撤销状态。
- src/chanlun_trader/research_factory/synthetic_batch_console.py：仅从已声明来源复核候选、额度及历史。
- src/chanlun_trader/webapp.py：正式本机批次API，复用原执行策略。
- frontend/src/console/components/SyntheticBatchConsole.vue：真实预览/确认/运行/控制和历史查询界面。
- frontend/src/console/ResearchConsole.vue：在统一工作台装配批次组件。
- tests/research_factory/test_synthetic_batch_web.py：真实服务API正负向、只读和旧入口兼容。
- tests/research_factory/synthetic_batch_demo.py：正式创建两候选的真实页面验收装配，不自动批准批次/使用资格。
- docs/SYNTHETIC_BATCH_AUTHORIZATION_V1.md：接口、启动和阶段证据说明。
- progress.md：本轮结果与限制记录。
- 回滚：git revert 本模块提交，停用新API页面但保留合成批准、预算和账本历史；检查点d104910。不修改main。
- 总工程仍PARTIAL，继续固定新HEAD整体回归、双平台认证、新版需求证据矩阵与集中审计；全部旧OPEN事件继续保留。
