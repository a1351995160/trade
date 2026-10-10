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

## 2026-09-10 - Task: 新版本全路线矩阵与集中审计导航
### What was done
- 汇总A/B及原新颖性/测试使用资格四项独立批准后的当前全路线矩阵，旧待批段落明确作为历史；新增V2审计导航、实际调用图、部署/恢复和限制。
- 同一正式创建的两候选批次链继续经独立用途确认、组合归档和Paper回放，未以研究BLOCKED结果冒充真实合格策略。
### Testing
- 真实页面同根贯通：批次4/4完成→两个独立用途资格确认→组合计划同股只分配一个策略（另一个NO_TRADE）→归档→所选候选9/600模拟事件、2笔实际合成成交→撤销后仍9事件且勾选确认也不能继续。真实策略0、观察0。batch-ui-paper.json与batch-ui-revoked.json保留原HTTP完整结果，服务器日志保留实际调用。
- 前端原presentation 8 passed，batch-ui-presentation.log；页面控件选择器两次定位失败后依据实际DOM精确定位，不将自动化定位错误视为业务缺陷。
- 文档链接与代码/测试路径核对、git diff --check。固定HEAD的整体回归与双平台下一步执行，未预填结果。
### Notes
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：新增当前全路线矩阵，更新四项批准实施状态。
- docs/ROADMAP_CONTINUOUS_DELIVERY_AUDIT_V2.md：新证据版本、实际调用、审阅分组、部署/恢复和未认证范围；旧V1及ZIP不改写。
- progress.md：追加贯通验收及固定版本前状态。
- 回滚：git revert本轮文档提交；代码检查点36d8a2c，不改主线或任何合成批准/账本。
- 最终认证和打包未完成前仍PARTIAL；最终状态放外部final-bounded-execution，不为状态回填修改固定HEAD。

## 2026-09-10 - Task: 批次worker固定命令与数据传输
### What was done
- 根据Sonar S6350实际污点链，将生产worker命令固定，身份经资源握手JSON传入并严格验证；原父批准、进程关系、资源、预算和历史语义不变。
- 未证明旧shell=False且标识已验入口可被利用，不伪造业务漏洞red；未改变Sonar规则、问题状态或添加抑制。
### Testing
- batch-stdin.log/XML：18 passed、22 deselected定向验证，1既有Starlette警告；真实OS上限、API两动作、两候选、中断及三类并发边界均通过，禁止进程/网络/受保护访问探针0。新增6个数据上下文合同负向。
- abb0073的Sonar原检查、问题流、annotations已保存；本地固定HEAD候选验证前7步通过（P3C107），第8步因本项必要收紧主动停止，未生成最终JUnit，不宣称整体通过。停止该测试进程后才修改源码，没有混用新旧代码证据。
### Notes
- src/chanlun_trader/research_factory/synthetic_batch.py：固定生产启动命令，身份进入数据通道。
- src/chanlun_trader/synthetic_batch_resources.py：原资源握手携带execution数据。
- src/chanlun_trader/synthetic_batch_worker.py：严格校验执行数据，保留原服务授权核验。
- tests/research_factory/batch_interruption_worker.py：退出注入使用实际握手身份。
- tests/research_factory/batch_boundary_race_worker.py：并发注入使用实际握手身份。
- tests/research_factory/test_synthetic_batch_contract.py：真实退出替换入口沿用数据握手。
- tests/research_factory/test_synthetic_batch_races.py：真实边界替换入口不接受身份命令参数。
- tests/research_factory/test_synthetic_batch_resources.py：6类无效数据合同拒绝。
- tests/research_factory/test_synthetic_batch_web.py：观察真实启动，断言命令固定及身份经数据传输。
- docs/SYNTHETIC_BATCH_AUTHORIZATION_V1.md：收紧依据及定向证据。
- docs/SYNTHETIC_BATCH_RESOURCES_V1.md：数据握手说明。
- CLAUDE.md：记录固定进程入口约定。
- progress.md：本轮验证和未完成认证记录。
- 回滚：git revert本轮提交，并停用受影响新入口；检查点abb0073，保留全部原始证据与账本，不改main。

## 2026-09-10 - Task: 在原时限内拆分完整服务认证
### What was done
- 同一R1认证工作流增加独立R2/R3正式服务job，原基础回归与新服务测试分组，不重复整套工作流，不增大时限。
### Testing
- 36d8a2c PR34493772051与push34493759369：Ubuntu通过、Windows20分钟超时取消；PR annotation明确最大执行时限20m0s，原日志与batch-ui-ci-timeout-annotations.json保存，未将其标记通过。
- validate-ci-partition.py和ci-partition-validation.json：28个原测试路径=21个基础+7个正式服务，互不重叠、并集精确一致；原Phase1/2/3B/3C完整命令、matrix/env、依赖锁和20分钟时限一致。未扩大skip、超时或隔离白名单。
- YAML解析与git diff --check通过；最终固定HEAD重新运行全部本地及两个平台，结果待实际完成。
### Notes
- .github/workflows/r1-source-data-certification.yml：按依赖范围拆分两组job与原始附件，原平台/版本/隔离保留。
- docs/ROADMAP_CONTINUOUS_DELIVERY_AUDIT_V2.md：记录实际超时依据、精确覆盖并集和认证要求。
- progress.md：追加本轮验证与限制。
- 回滚：git revert本轮提交，检查点08e3146；不能以回滚分组掩盖超时，不改main或运行权限。

## 2026-09-11 - Task: CA-01 使用资格提交状态完整性
### What was done
- 接收并核验ffbff08集中审计包，按用户统一修正CA-01至CA-04的范围继续原分支及Draft PR9。
- 真实服务请求、确认、撤销后移走单个撤销文件，独立新进程实际恢复ACTIVE并把Paper9事件推进至10，取得项目red；外审AST探针单列，不冒充E2E。
- 新v2请求增加每请求提交头，绑定不可变请求/确认/撤销证据序列；缺事件、缺/坏头、旧头回退、写点中断均阻断。不补历史、不改引擎或权限边界。
- 旧v1回执只读，实际原审计合成回执作为兼容夹具，附来源和原字节哈希；新的合法v2请求仍可正常使用。
### Testing
- ca01-red.log/xml：1项真实服务冷进程复现失败，inspect/active/preview放行，实际advance至10；原子进程输出保留。
- ca01-green.log/xml：原使用资格及新增冷进程复现16 passed。
- ca01-contract.log/xml：使用资格/工作台31 passed，包括缺/坏head、缺确认/撤销、旧head、API拒绝不修复、真实os._exit(73)确认/撤销写点与新进程拒绝、原回执兼容。
- ca01-race.log/xml：1 passed/24 deselected，仅新增真实Paper与撤销共享互斥竞争；不是全量重复计数。进程保护访问/网络/禁止进程探针均0，原Starlette警告保留。
- 证据根E:/llmwiki/roadmap-engineering-evidence/ca-remediation-ffbff08；最终HEAD整体验证及双平台尚待四项完成。
### Notes
- src/chanlun_trader/research_factory/synthetic_usage.py：v2提交头、完整性核验和旧格式只读。
- tests/research_factory/test_synthetic_usage.py：实际服务red/green、缺文件、硬退出、API及锁竞争兼容测试。
- tests/research_factory/usage_integrity_worker.py：实际新进程消费与真实写点退出驱动，无假权限/领域替身。
- tests/research_factory/fixtures/ca01_legacy_usage/request.json、confirmation.json、revocation.json：原审计包中的实际合成旧回执原字节，仅历史兼容。
- tests/research_factory/fixtures/ca01_legacy_usage/provenance.json：旧回执来源与SHA256。
- docs/CONSOLIDATED_REMEDIATION_CA01_CA04.md：统一范围、执行矩阵、v2写点/兼容/故障策略。
- docs/ROADMAP_IMPLEMENTATION_MATRIX.md：标明外审CHANGES_REQUESTED及新修正索引。
- CLAUDE.md：记录撤销历史必须有提交见证的实现教训。
- progress.md：追加本轮记录。回滚点ffbff0856f95fdd3206ac70de6a82f3fd1164a16；可git revert本模块提交，但须先停用新资格入口并保留v2记录，不允许旧代码继续消费新批准。旧审计包不变；不merge、不auto-merge、不读真实数据。

## 2026-09-11 - Task: CA-02 新颖性canonical协议与旧入口防降级
### What was done
- 使用正式创建/设计/冻结/物化/Structural/新颖性确认/预测授权生成新意图，取得旧候选边界丢标签放行、来源stale后旧confirm/recover仍接受的真实服务red；该red未执行engine/绩效，不扩大结论。
- 原意图读盘与运行协议校验分离；旧运行入口和候选重建拒绝新版意图，性能边界先核canonical身份，不能由缺metadata决定legacy。
- 保留原Gate/预算和批次协议；真实旧v1启动记录通过原服务生成，兼容正常且无历史迁移。
### Testing
- ca02-red.log/xml：1 failed/2 deselected，实际原代码缺标签/旧入口行为复现；原局部探针另存received，不作为本测试。
- ca02-green.log/xml：27 passed，正式完整R2/同Trial硬退出恢复/完成回放、新意图及原新颖性边界。
- ca02-integration.log/xml：5 passed，最终实际执行器缺标签拒绝、旧入口拒绝、批次反降级和原治理生命周期。原始worker记录engine0/performance0，拒绝后原预算三桶used0/reserved0。
- ca02-legacy.log/xml：1 passed/3 deselected，原服务创建真实旧v1启动意图，confirm/recover兼容且预算原字节不变。
- 禁止访问/网络/进程探针0；各日志和原始worker输出保留于ca-remediation-ffbff08。git diff --check通过。最终新HEAD认证待四项完成。
### Notes
- src/chanlun_trader/research_factory/predictive_trial_start.py：canonical仅读取与运行协议检查分离，旧入口/候选构造拒绝新版。
- src/chanlun_trader/research_factory/synthetic_novelty_start.py：显式声明新版本路由，仍用原领域门禁。
- src/chanlun_trader/research_factory/synthetic_novelty.py：缺metadata前先核持久协议，保留原比较算法。
- src/chanlun_trader/research_factory/synthetic_batch_delegation.py：反降级检查使用canonical仅读取，不扩权。
- tests/research_factory/r2_service_worker.py：原真实服务驱动新增只准备新版/旧版实际意图模式，不运行pytest领域替身。
- tests/research_factory/novelty_protocol_worker.py：真实边界、旧入口、实际执行器及计数/原预算取证。
- tests/research_factory/test_r2_formal_services.py：新版反降级与真正旧v1兼容回归。
- .gitattributes：仅CA-01原审计JSON夹具禁换行转换，保持跨平台来源字节哈希；git check-attr text=unset。
- docs/CONSOLIDATED_REMEDIATION_CA01_CA04.md：更新CA-02证据、路由和范围；CLAUDE.md追加教训；progress.md追加记录。
- 回滚点b17914b5e41114deebab00dd9f897da6a45983a1；可git revert本模块提交，但须保持新版启动禁用，不能重新使用旧入口消费新版意图。Git自动gc报告旧不可达对象较多，未执行prune/清理，无用户数据操作。

## 2026-09-11 - Task: CA-03 Paper已提交尾部丢失检测
### What was done
- 实际引擎提交9条后移走尾部，取得新进程误读8条并推进10条的服务red；新增v2提交头，读盘/恢复/advance先核验提交边界。
- 沿用共享资源互斥阻止陈旧实例覆盖；旧v1归档只读且明确未验证，不迁移、不补历史。
### Testing
- ca03-red.log/xml：1 failed/8 deselected；原始新进程stdout保留。ca03-green.log/xml：9 passed。
- ca03-contract.log/xml：25 passed，覆盖多条尾部丢失、head缺失/损坏/回退、真实进程提交前后退出、旧实际归档兼容、原engine完整对账及工作台。
- ca03-api.log/xml：2 passed/6 deselected，内容损坏与尾部丢失均GET/POST 409。隔离探针0；保留原Starlette警告。最终HEAD全套认证待完成CA-04。
### Notes
- src/chanlun_trader/research_factory/paper_replay.py：显式v2头、提交见证、完整性复核、旧历史只读、既有资源锁。
- tests/research_factory/test_paper_replay.py：实际red/green和提交写点/兼容负向；paper_replay_worker.py：实际提交点硬退出；paper_integrity_worker.py：真实新进程读取/advance取证。
- tests/research_factory/test_engineering_workbench.py：增补尾部丢失API阻断。
- tests/research_factory/fixtures/ca03_legacy_paper/header.json、events/00000000.json至00000008.json、provenance.json：原审计包真实合成归档原字节与来源；.gitattributes限定禁止这些JSON换行转换。
- docs/CONSOLIDATED_REMEDIATION_CA01_CA04.md：版本/恢复/兼容与证据；CLAUDE.md追加教训；progress.md追加记录。
- 回滚点72fb21fc5b895c116d26d9413633ddbf3a51291f；可git revert本模块提交，但须停用v2会话推进并保留目录，不能旧代码消费新记录。未读取真实数据；既有OPEN事件不变。

## 2026-09-11 - Task: CA-04 完整计划内容当前性与源码身份
### What was done
- 正式预览及真实比较接口复现四类有效内容变化误报CURRENT，增加完整plan_id比较并保留原上下文失效解释。
- 新source_identity版本覆盖实际计划、compiler/helper和引擎成本/仓位依赖；旧计划只读可比，不迁移身份，不改变策略规则。
### Testing
- ca04-red.log/xml：4 failed/12 deselected，条目/持仓/就绪原因/诊断的有效新内容身份误报；这是比较接口反例，不是引擎输出不同的声称。
- ca04-green.log/xml：54 passed，实际计划确定性、只读存档、源码依赖身份、组合、Paper引擎对账和工作台API；隔离探针0，保留原Starlette警告。
- git diff --check通过；四项模块闭环完成后固定HEAD运行原选择器整体及双平台CI，结果保存在新审计包，不预写通过。
### Notes
- src/chanlun_trader/research_factory/daily_plan.py：完整plan_id当前性检查及显式来源身份v2。
- tests/research_factory/test_daily_plan.py：四种实际比较接口red/green、依赖源码身份变化但计划条目保持一致的验证。
- docs/CONSOLIDATED_REMEDIATION_CA01_CA04.md：四项矩阵、CA-04证据、部署/回退及限制；CLAUDE.md追加教训；progress.md追加本轮。
- 回滚点51834c59c17e16e23a9a1fb6136c60181f0b83c5，可git revert本模块提交；回退后禁止依赖旧CURRENT判定授予用途。旧审计包与原检查不变，无真实数据或运行。

## 2026-09-11 - Task: CA-03旧原字节夹具跨平台封装
### What was done
- 修复候选4fdc841在PR Linux空白门禁中的夹具封装失败；原10份旧JSON原字节装入archive.zip，原来源哈希不变，provenance新增容器哈希。
- 不转码旧批准/事件、不添加空白豁免，移除CA-03 -text规则；旧格式兼容测试解包后校验全部原字节。
### Testing
- ca03-container.log/xml：1 passed/17 deselected，旧归档完整性与只读兼容通过。
- final-4fdc841保存原push/PR日志与失败；本地6阶段完成、P3C主动中断并记interruption.json，不作为最终认证。因实际CI失败而重建认证HEAD，不无理由重跑。
- 按Linux默认空白规则检查完整base差异；最终新HEAD重新运行全部认证，不扩大skip/timeout或隔离白名单。
### Notes
- tests/research_factory/fixtures/ca03_legacy_paper/archive.zip：包含原header与9事件原字节；移除10个展开JSON，provenance.json保留各来源哈希并记录容器。
- tests/research_factory/test_paper_replay.py：显式读容器和全部成员hash再写隔离夹具；.gitattributes移除CA-03特殊text规则。
- docs/CONSOLIDATED_REMEDIATION_CA01_CA04.md记录真实CI失败/封装变更；CLAUDE.md追加教训；progress.md追加日志。
- 回滚点4fdc8418de87de50e350510d05198560e5ba2c75，可git revert本提交恢复展开夹具；将恢复原Linux空白门禁失败，不得据此绕过检查。生产代码无变化。

- 同轮显式Linux规则检查进一步检出CA-01旧回执CRLF，原失败输出保留linux-whitespace-green.log（文件名不代表通过）。CA-01也改为archive.zip保存原三回执，provenance原来源哈希不变；test_synthetic_usage.py逐项验证后使用；本轮新增.gitattributes全部移除，无空白豁免。
- legacy-containers.log/xml：2 passed/41 deselected，两种原字节容器兼容均通过；最终空白复核写入新的linux-whitespace-final.log，不覆盖前次失败。

## 2026-09-12 - Task: 发布受限研究及固定账户代码
### What was done
- 在main基线上建立代码发布分支，仅移入研究适配、受限执行、共享固定账户规则、只读预览及合成测试。
- 本地研究历史、真实数据、绩效、批准及消费回执保留在原工作区，不纳入公开Git历史。
### Testing
- 全部77个移入代码/测试文件与原已提交Git blob逐字节一致。
- 新增及修改测试201 passed in 12.72s；隔离模式，NTFS临时目录；禁止执行和授权探针全部0。git diff --check通过。
### Notes
- scripts/：受限数据准备、证据核验、执行及进度工具。
- src/chanlun_trader/data/tdx/、engine/、research/、research_factory/：数据适配、原账户/治理接线及共享规则。
- tests/research/、tests/research_factory/：对应合成回归。
- docs/BOUNDED_RESEARCH_CODE.md：公开使用范围和权限约束；progress.md追加本条发布记录。
- 回滚点fed519b；可git revert本次发布提交，不能以代码回滚删除或重置本机实际研究回执。Windows OPEN保留。

## 2026-09-12 - Task: 修复公开检查发现的证据路径边界
### What was done
- 对修复证明及其引用、OWNER请求/授权、恢复TDX来源和准备输入实施解析后的目录边界验证；保留既有哈希与窗口检查。
- 用pd.isna表达NaN断言，不改变验证含义；将新增路径依赖纳入执行源码身份。
### Testing
- 针对性56项通过，包括越界、相似目录前缀拒绝以及原治理、OWNER和价格适配回归；执行探针0。
- 不修改或关闭SonarCloud规则，不调整现有CI skip/timeout；等待远端重新验证。
### Notes
- scripts/run_train_account_v1.py、run_degraded_account_v2.py、build_owner_execution_package.py、validate_owner_execution_package.py、execute_baostock_account_v1.py：边界验证与源码依赖。
- src/chanlun_trader/research_factory/evidence_paths.py、exploration_governance.py：目录约束与修复证据引用保护。
- tests/research_factory/test_evidence_paths.py、test_baostock_price_views_v1.py：拒绝边界和明确NaN断言。
- docs/BOUNDED_RESEARCH_CODE.md、progress.md：公开使用说明及本条记录。
- 回滚点dab691d；可git revert本次安全修复，不影响本地研究分支或外部证据。

## 2026-09-12 - Task: 保持修复回执身份与源码依赖完整
### What was done
- 路径安全检查仅使用局部解析结果，保留原repair载荷及其身份哈希；旧训练入口同时绑定新增路径模块。
### Testing
- 目录边界和受限探索治理回归通过，未改变原幂等载荷。
### Notes
- src/chanlun_trader/research_factory/exploration_governance.py：分离检查路径和原回执身份。
- scripts/run_train_account_v1.py：补入源码依赖；progress.md追加。
- 回滚点3e3d86a；可git revert本提交。

## 2026-09-12 - Task: 补齐首次来源与派生证明的路径检查
### What was done
- 在任何解析符号链接的文件系统操作前先作词法目录检查，然后再校验解析结果；补齐初次OWNER来源、因子registry及训练修复红绿证明读取边界。
### Testing
- 路径、探索治理、训练增量和OWNER回归通过，保留全部原断言。
### Notes
- scripts/build_owner_execution_package.py、scripts/run_train_account_v1.py、src/chanlun_trader/research_factory/evidence_paths.py、train_execution_governance_v1.py：补齐读取前检查；progress.md追加。
- 回滚点893f9a6；可git revert本次修复。

## 2026-09-12 - Task: 完整覆盖OWNER日线读取路径
### What was done
- OWNER所有初次、预热及恢复读取共用同一目录边界；词法检查使用带目录分隔符的规范化前缀，解析符号链接后继续检查真实归属。
### Testing
- 路径穿越、相似前缀拒绝及OWNER回归通过；不扩大许可目录或跳过窗口检查。
### Notes
- scripts/build_owner_execution_package.py、src/chanlun_trader/research_factory/evidence_paths.py：完整读取边界；progress.md追加。
- 回滚点5dbe986；可git revert本次修改。

## 2026-09-12 - Task: 发布技术研究入口与合成验证
### What was done
- 从当前main建立代码发布分支，保留既有证据路径校验。接通版本化技术信号、原账户执行和只读报告程序；不发布私有研究日志、输入、回执或真实绩效。
### Testing
- 针对技术公式、旧合同、账户退出、信号预览与路径边界执行合成回归；实际结果将在本轮验证完成后追加。
### Notes
- scripts/run_train_search_batch_v1.py、scripts/report_train_search_v1.py、scripts/report_technical_train_v1.py：治理执行和本地结果报告。
- src/chanlun_trader/research_factory/train_search_batch_v1.py、technical_train_signals_v1.py、fixed_account_rules.py：明确版本规则，旧合同仍受原检查。
- tests/research_factory/test_train_search_batch_v1.py、test_technical_train_signals_v1.py：合成手算及治理回归。
- docs/TECHNICAL_RESEARCH_SCOPE_V1.md：方法范围与冻结口径；docs/BOUNDED_RESEARCH_CODE.md：新增用法说明；progress.md仅追加公开工程信息。
- 回滚点c323677；可git revert本次提交，不回滚或改写本机研究账目。

### Testing（完成）
- 65项针对性合成回归通过，7.39秒；git diff --check通过。

## 2026-09-14 - Task: 合并累计研究代码并保留main安全修复
### What was done
在独立工作区整合main与研究分支；保留main已有证据路径边界修复和研究分支新策略/窗口/统一账户能力。main公开progress历史保留，研究工作区完整原日志仍在本机不动。
### Testing
合并组合64项通过，3项历史授权到期失败；限定合成测试时钟后该文件13项通过。首次复用测试时钟产生循环导入，改为模块内合成时钟后收集及测试通过；生产时钟/期限不改。远端SonarCloud旧提交质量门禁未通过，未绕过。
### Notes
冲突文件逐项按两侧实际差异解决，保留evidence_paths及owner/repair引用校验；test_windowed_actions_and_train_grant.py仅合成时钟稳定化。原工作区未切分支、未stash、未改数据及预算。回滚采用git revert -m 1本合并提交，不reset原工作区。

## 2026-09-15 - Task: 回测行为验收 V1（指标、日线退出与公共入口）
### What was done
核清旧 BacktestRunner、V2 BacktestEngineV2、经典 Web 回测接口与研究服务的实际调用关系，建立版本化指标与日线退出口径并接通公共 API/CLI。补齐 MACD_V1、五类分别命名的 MACD 条件、KDJ(9,3,3)、MA/EMA/CROSS，以及"收盘判断、次一 session 开盘尝试执行"的固定成本止损、固定止盈、最高收盘价移动止损、固定持有退出；成本锚为该 lot 实际成交价。修复旧入口大盘过滤在开盘读取当日未来收盘的前视缺陷，并修正日线撮合在容量为零时退回全量成交的行为。盘中触价、Tick/盘口与多周期请求明确拒绝，未静默降级。
### Testing
在 BASE_SHA=d14178f 的干净工作树与本轮分支分别运行完整 pytest：失败集合完全一致（335 项，均为缺少本机研究数据/未构建前端等既有环境失败），无新增失败、无回归；通过数由 1711 增至 1803。本轮新增 92 项测试（公式 oracle、四类退出、完整账户链 A-J、独立手算账本、API/CLI 语义一致、旧入口前视红转绿）全部通过，JUnit 已生成。旧入口前视与容量为零两项为真实业务 red 到 green。未执行真实行情、真实绩效或真实研究授权。
### Notes
- src/chanlun_trader/engine/indicators_v1.py、conditions_v1.py、daily_exit_v1.py、behavior_service_v1.py：新增版本化指标、条件、日线退出与显式模式服务。
- src/chanlun_trader/backtest.py：修复旧入口大盘过滤开盘前视并 fail-closed。
- src/chanlun_trader/engine/fill.py：容量为零时拒绝成交，不退回全量。
- src/chanlun_trader/execution_policy.py、webapp.py：新增默认关闭的只读计算开关与行为回测入口、合同只读端点。
- scripts/run_behavior_backtest_v1.py、scripts/emit_behavior_acceptance_evidence_v1.py：CLI 入口与证据生成。
- tests/indicators、tests/behavior、tests/legacy_entry：公式、退出、账户链、手算账本、公共入口与旧入口回归。
- docs/BACKTEST_BEHAVIOR_ACCEPTANCE_V1.md、reports/behavior_acceptance_v1/CAPABILITY_MATRIX.json：口径、入口、能力矩阵与未支持范围。
- .github/workflows/backtest-behavior-acceptance.yml：双平台 CI 与 JUnit 上传。
- 回滚点 d14178f；可 git revert 本轮提交，不影响旧目录与既有研究账目。

### Testing（V1 收尾追加）
- 以 CI 同等隔离环境（CHANLUN_TEST_ISOLATION=1）在 BASE_SHA 干净工作树与本分支分别运行完整 pytest：失败集合逐条一致（193 项，均为本机缺少研究数据/前端未构建等既有环境失败），零新增失败、零回归；通过数 1853 增至 1963。
- SonarCloud 4 条 SECURITY 项（2 HIGH 路径穿越 + 2 MEDIUM workflow 依赖未锁定）全部按证据以代码修复关闭，未删除检查、未扩大排除项、未加抑制标注；新增路径越界回归测试。
- 正式估值接线：改为经 official_equity_curve 抽取，证明独立日历必填、缺整日/缺末日抛错、NaN/Inf 拒绝；入口增加声明日历的数据覆盖校验。修复 official_equity_curve 非数值权益泄漏 ValueError 的缺陷。

## 2026-09-22 - Task: 可扩展回测验收 V2（通用指标、规则组合与账户链扩展）
### What was done
在已合并的 V1 main（c498ee2）上建立独立分支，复用 V1 的指标、条件、退出与引擎链，按家族增量实现通用指标体系、统一注册、受限条件组合与退出扩展。指标从 MACD/KDJ 扩到 7 家族 51 项（均线趋势、动量震荡、波动通道、量价资金代理、价格结构、统计截面、自定义组合），每项带机器可读契约（输出名、参数、预热、缺失政策、可用时间、单位、额外数据依赖）。新增受限表达式层支持比较/AND-OR-NOT 三值逻辑/交叉/位置/区间/滚动逻辑/截面排名；NOT(UNKNOWN) 保持 UNKNOWN，未 ready 与 NaN 不产生交易资格。退出在 V1 四类之上新增 ATR 距离、ATR 跟踪、指标条件、反向信号与明确版本结构价退出，结构止损与成本止损分别命名。公共 API/CLI/Web 三入口使用同一服务，合同端点由注册表动态提供，界面不硬编码指标。
### Testing
V2 新增 115 项测试全部通过（公式 oracle、因果性、边界、三值逻辑、安全拒绝、退出、完整账户链、三入口语义一致），JUnit 已落盘。V1 兼容回归 139 项通过。完整套件在 BASE_SHA 干净工作树与 V2 HEAD 分别运行：失败总数一致（各 170），差异项为既有顺序相关不稳定（Windows GBK 解码/共享状态），单独运行均通过，且 V2 模块未被 research_factory 引用。未读取真实行情、未运行真实绩效。
### Notes
- src/chanlun_trader/engine/indicators_v2.py、indicator_registry_v2.py、conditions_v2.py、daily_exit_v2.py、custom_indicators_v2.py、behavior_service_v2.py：多家族指标、注册契约、表达式层、退出扩展、自定义 fixture 与注册表驱动服务。
- src/chanlun_trader/webapp.py：新增合同端点（注册表驱动）与 V2 回测端点，只读计算白名单扩展。
- scripts/run_behavior_backtest_v2.py、scripts/emit_v2_acceptance_scope_v1.py：CLI 与验收证据生成。
- frontend/src/components/GenericBacktestPanel.vue、frontend/src/App.vue：注册表驱动的通用回测配置表页。
- tests/indicators_v2、tests/conditions_v2、tests/exits_v2、tests/entrypoints_v2：V2 新增测试。
- docs/EXTENSIBLE_BACKTEST_ACCEPTANCE_V2.md、ACCEPTANCE_SCOPE.json、reports/v2_acceptance/：口径、范围、盘点、矩阵与回归证据。
- .github/workflows/extensible-backtest-acceptance-v2.yml：双平台 CI，V1 兼容与 V2 新增分开执行并上传 JUnit。
- 回滚点 c498ee2；可 git revert 本轮提交，不影响 V1 与受保护旧目录。

### Testing（PR #15 定向复核修正追加）
- 复现并关闭五类根因：指标实例覆盖、ATR 入场锚前视与参数忽略、多证券共用退出上下文、Top-N UNKNOWN 处理、证据矩阵前缀判定与实现指纹。
- 修复过程中另发现并关闭四项真实缺陷：DMI/ADX 漏除 window 导致 ADX 放大约 N 倍、Wilder 种子被 NaN 污染、DMI 段首 TR 口径与 true_range 不一致、DYNAMIC_CURRENT 在首根被跳过。
- 新增 tests/pr15_remediation（25 项），全部经真实 API/CLI 取得 red/green；V2 新增测试合计 141 项通过，V1 兼容回归 139 项通过。
- 完整套件在 BASE_SHA 干净工作树与本次 HEAD 分别运行：零新增失败、零回归（基线 194 项失败集合覆盖本次 193 项；差异为既有顺序相关不稳定）。
### Notes
- src/chanlun_trader/engine/indicator_registry_v2.py、behavior_service_v2.py、conditions_v2.py、daily_exit_v2.py、custom_indicators_v2.py、indicators_v2.py、engine.py：实例身份、ATR 依赖与冻结锚、按证券上下文、Top-N 合格截面、公式指纹、ADX 修正与 lot 创建回调。
- tests/pr15_remediation/test_pr15_remediation_v1.py、tests/indicators_v2：五类根因验收与 DMI/SAR 独立 oracle。
- scripts/emit_v2_acceptance_scope_v1.py、docs/EXTENSIBLE_BACKTEST_ACCEPTANCE_V2.md、reports/v2_acceptance/：逐项证据矩阵与口径更新。
- .github/workflows/extensible-backtest-acceptance-v2.yml：V2 新增测试纳入双平台 CI。
- 回滚点 196e666；可 git revert 本次提交。

### Testing（PR15 残留修正追加）
- 复现并关闭四处残留：表达式内版本未核验、ATR 序列被最后一条规则覆盖且窗口从契约默认值重推、排名退出因按 session 取值而永不触发、矩阵证据只写目录名且指纹只拼依赖名称。
- 另修一处真实缺陷：服务路径未把 ATR 绑定身份传给冻结锚，导致 trace 无法溯源实例。
- 新增 tests/pr15_residual（20 项），全部经真实 API/CLI 取得 red/green；PR15 定向两轮合计 46 项通过，V2 主体 120 项通过，V1 兼容回归 139 项通过。
- 矩阵新增 nodeid 实存校验测试：矩阵声称的 16 个 nodeid 全部能被 pytest 真实收集，写错即测试失败（发现并修正了 MACD/KDJ 两个错误 nodeid）。
- 完整套件在 BASE_SHA 干净工作树与本次 HEAD 分别运行：零新增失败 nodeid（基线 194 项失败集合覆盖本次 193 项）；因果未确认，不声明全系统零回归。
### Notes
- src/chanlun_trader/engine/behavior_service_v2.py：表达式引用解析为精确实例键并核验版本；每条 ATR 规则各自持有序列与绑定身份；窗口取实际使用值；混合截面/时序条件运行前拒绝。
- src/chanlun_trader/engine/daily_exit_v2.py：入场锚按 (lot_id, rule) 存储；排名退出按结果索引域取值（symbol 或 session）；绑定身份随 trace 输出。
- src/chanlun_trader/engine/indicator_registry_v2.py：IndicatorResult 携带 resolved_params；公式指纹递归绑定依赖 version 与源码。
- scripts/emit_v2_acceptance_scope_v1.py、docs/EXTENSIBLE_BACKTEST_ACCEPTANCE_V2.md、reports/v2_acceptance/：逐项精确 nodeid + 适用域 + JUnit，测试分母按来源分开，OPEN 项与因果未确认如实登记。
- tests/pr15_residual/test_pr15_residual_v1.py：残留验收（版本绑定、双 ATR 规则、排名退出、指纹变异、nodeid 实存）。
- tests/pr15_remediation、tests/exits_v2：同步按规则登记锚与 nodeid 校验。
- .github/workflows/extensible-backtest-acceptance-v2.yml：残留测试纳入双平台 CI。
- 回滚点 ae871ae；可 git revert 本次提交。

### Testing（证据适用性与依赖指纹收尾）
- 复现并修复依赖版本指纹缺陷：此前 _formula_hash 遍历 specs() 取第一个版本，而 get(id) 取最大版本，导致「执行选 V2 却哈希 V1」。现与执行共享同一解析，支持显式固定版本，缺依赖/歧义/循环明确拒绝。
- 修正证据适用性：建立受审映射（指标→真实调用其实现的 oracle 测试；指标→真实消费其 indicator_id 的入口测试；条件维度→覆盖该维度正向语义的测试），逐维度分别判定。VERIFIED 从 28 项诚实降为 10 项——此前把所有 51 个指标名写进入口集合，等于用 RSI/EMA 的入口测试给其余 47 项作证。
- MACD/KDJ 的 oracle 正确关联到 V1 兼容 JUnit（参数化节点），不再错误关联 V2 套件。
- 新增负向检查：入口证据错设为只消费 RSI/EMA 的测试、条件维度共用 Top-N 测试、REF/arithmetic 仅有负向测试而误升 VERIFIED，均会被测试拒绝。
- 强化排名换位测试：用明确两证券、日期与预期排名断言，替代原先的非空断言。
- PR15 定向两轮 57 项通过；V2 主体 120 项通过；V1 兼容回归 139 项通过。
- 完整套件在 BASE_SHA 干净工作树与本次 HEAD 分别运行：零新增失败 nodeid（基线 194 覆盖本次 193）；因果未确认，不声明全系统零回归。
### Notes
- src/chanlun_trader/engine/indicator_registry_v2.py：依赖解析与指纹共享同一确定结果；支持 pinned_versions；缺依赖/循环依赖抛 DependencyResolutionError。
- scripts/emit_v2_acceptance_scope_v1.py：受审证据映射（ORACLE_NODEIDS / ENTRYPOINT_NODEIDS / CONDITION_NODEIDS / CONDITION_PARTIAL_REASONS / ASSERTION_SUMMARY），逐维度判定与断言内容登记。
- tests/pr15_residual/test_pr15_residual_v1.py：依赖版本指纹六项测试、排名换位明确断言。
- tests/pr15_remediation/test_pr15_remediation_v1.py：四项证据适用性负向检查。
- docs/EXTENSIBLE_BACKTEST_ACCEPTANCE_V2.md、reports/v2_acceptance/、ACCEPTANCE_SCOPE.json：10/44、177/139、逐项原因与 OPEN 项同步。
- 回滚点 204ccaa；可 git revert 本次提交。

### Testing（依赖执行与指纹一致）
- 复现并修复 B1：pinned_versions 只影响指纹、不约束计算——父公式内部 compute('DEP') 仍取默认最新版，父输出 1→2→99 而父 hash 不变。新增 DependencyScope 与 registry.compute_dependency，父实现取依赖走同一解析结果，固定版本真实约束计算；作用域按调用栈嵌套，嵌套固定版本不被其他节点默认值覆盖。
- 复现并修复 B2：判环用单一 seen 集合，把合法共享依赖 PARENT→A、PARENT→B、B→A 误判为 CIRCULAR_DEPENDENCY:A。改为只针对当前递归路径判环，已完成节点缓存；共享依赖通过，真自循环与回边仍拒绝。
- 父公式真实消费依赖（compute_dependency 并把值写入自身输出），不用常数或伪造 trace。
- A 文字修正：10 VERIFIED 明确为 4 个指标 + 6 个条件函数维度，不是 10 项完整账户认证；补 cross_down 最小正例，CROSS 双向子维度分别记录；每条证据记录自己的 suite 与 JUnit（MACD/KDJ 在 V1 套件，共享契约测试在 V2 套件）；明确受审映射不能自动识别任意无关测试。
- 新增 tests/pr15_dependency（10 项）；条件层补 cross_down 正例。
- V2 新增 188 项通过；V1 兼容回归 139 项通过。
- 完整套件：失败总数与 BASE_SHA 基线一致（各 194）；差异项单独运行通过且未引用 V2 模块；因果未确认，不声明全系统零回归。
### Notes
- src/chanlun_trader/engine/indicator_registry_v2.py：新增 DependencyScope 与 compute_dependency（固定版本真实约束计算）；_formula_hash 判环只针对当前递归路径并缓存已完成节点；_source_of 支持合成源码覆盖。
- tests/pr15_dependency/test_dependency_execution_v1.py：B1/B2 定点验收（固定版本约束计算、默认解析驱动输出与指纹、嵌套固定、共享 DAG、真循环拒绝、注册顺序）。
- tests/conditions_v2/test_condition_layer_v2.py：补 cross_down 正向用例。
- tests/pr15_remediation/test_pr15_remediation_v1.py：适配每条证据自己的 suite 与 JUnit。
- scripts/emit_v2_acceptance_scope_v1.py、reports/v2_acceptance/、ACCEPTANCE_SCOPE.json：CONDITION_EVIDENCE 子维度与 per-suite JUnit；分母 188/139。
- docs/EXTENSIBLE_BACKTEST_ACCEPTANCE_V2.md：§6.6 依赖执行与指纹；10 VERIFIED 含义澄清。
- .github/workflows/extensible-backtest-acceptance-v2.yml：纳入 tests/pr15_dependency。
- 回滚点 c9a63ed；可 git revert 本次提交。

### Testing（依赖作用域防误用）
- 复现绕过：pinned DEP_V1 时，父实现直接 registry.compute("DEP") 仍取默认最新版 DEP_V2（父输出 2.0），而 hash 按 DEP_V1 算——防护缺口。
- 修复：依赖求值期间，若该指标在作用域里已被固定版本，未显式传 version 的直接 compute() 抛 DIRECT_COMPUTE_BYPASSES_PINNED_DEPENDENCY 并指出正确做法。
- 边界：依赖未固定时不误报；显式传 version 不算绕过；compute_dependency 内部带标记不会被自身防护拦下。
- 修正仓库内真实绕过点：RsiRegimeProvider 原直接 registry.compute("RSI")，改为 compute_dependency，并为 RSI_REGIME_FLAG 登记 pinned_versions={"RSI":"RSI_V1"}。
- 新增 4 项防护测试（绕过拒绝、未固定不误报、显式版本放行、内置依赖型指标走依赖路径）。
- V2 新增 192 项通过；V1 兼容回归 139 项通过。
- 完整套件：本次 HEAD 零新增失败 nodeid（基线 194 覆盖本次 193）；因果未确认，不声明全系统零回归。
### Notes
- src/chanlun_trader/engine/indicator_registry_v2.py：compute 增加 _via_dependency 标记与绕过防护；compute_dependency 标记来源。
- src/chanlun_trader/engine/custom_indicators_v2.py：RsiRegimeProvider 改用 compute_dependency；RSI_REGIME_FLAG 登记 RSI 固定版本。
- tests/pr15_dependency/test_dependency_execution_v1.py：新增四项防护测试（共 14 项）。
- scripts/emit_v2_acceptance_scope_v1.py、reports/v2_acceptance/、ACCEPTANCE_SCOPE.json、docs/EXTENSIBLE_BACKTEST_ACCEPTANCE_V2.md：分母 192/139、§6.7 防误用与风险降级说明。
- 回滚点 c6af41a；可 git revert 本次提交。

## 2026-09-23 - Task: 纯指标验收 V1（公式/条件/合成入口 + 真实 RAW 小样本）
### What was done
按五份盘点材料推进现有 OHLC(VA) 指标的公式、参数、条件消费与合成入口验收，不开展盈利搜索。先在含 V1/V2 的 main（f5acb03）新建干净工作区，旧工作区与数据未动。实现 gbbq 访问边界并在真实访问链上证明拒绝先于文件打开；更正上一轮盘点的十项互相矛盾声明（原件保留）。A0 为 25 项此前缺 oracle 的指标补独立朴素参考与逐值对照，并补齐条件消费与合成账户接线；A1 在两证券、单一窗口上做真实 RAW 数值小样本。过程中复现并修复一个真实公式缺陷（PSY 恒为 100%）。
### Testing
- 访问边界：8 项守卫测试（open 探针证明拒绝在打开前、覆盖直接与间接调用、全量缓存同样禁止、合成夹具不误伤、环境变量不能放开、正常价格路径正对照）；4 项有界读取边界测试（合成 .day 上证明物理限窗、拒绝越过封存期、物化量不随文件增长）。实际生效证据：原会真实读取 gbbq 的测试从 29.85s 通过变为 0.56s 内失败于 FORBIDDEN_GBBQ_ACCESS，未读取文件，已改为显式 skip。
- A0：公式增量 158 项 + 条件消费 41 项全部通过；覆盖两个以上参数、常数/递增/递减/振荡/跳变/缺口/零量/非有限/短于预热、追加未来不改历史、条件 TRUE/FALSE/UNKNOWN 正反例、不可能条件不产生成交、25 项经公开服务进入合成账户链产生真实成交。
- 真实缺陷：PSY 分子 counts 与分母 totals 数学恒等导致恒输出 100%；交替涨跌序列复现（期望约 50%）；修复后新增三项回归测试；既有测试全部通过说明原测试未覆盖振荡序列。
- A1：600000.SH / 000001.SZ，2024-01-02..2024-07-31，每证券物化 140 行，max_date_materialized=20240731 未触碰封存期；8 个指标与独立参考逐值一致。
- 跨平台：修复 Linux 下 Path("E:/") 被当作相对路径导致守卫失效（Ubuntu 曾 fail、Windows pass），现双平台均通过。
- 本轮目标套件 491 项通过（另 2 项因 gbbq 禁令显式跳过）；完整套件零新增失败 nodeid（基线 194 覆盖本次 193）；因果未确认，不声明全系统零回归。
### Notes
- src/chanlun_trader/price_only_scope.py：新增 gbbq 路径边界（按真实路径身份、跨平台规范化、拒绝先于 open）。
- src/chanlun_trader/tdx_data.py：_load_gbbq 接入守卫。
- src/chanlun_trader/engine/indicators_v2.py：修复 PSY 分子/分母语义。
- tests/indicators_v2/_oracle_price_only_v1.py、test_price_only_formula_increment_v1.py：28 个独立 oracle 与 158 项公式验收。
- tests/conditions_v2/test_price_only_condition_consumption_v1.py：41 项条件消费与合成账户接线。
- tests/price_only_scope/{test_gbbq_access_guard_v1,test_bounded_read_boundary_v1,test_real_raw_sample_v1}.py：访问边界与真实 RAW 小样本。
- tests/pit/test_qfq_pit_safety.py、tests/test_tdx_data.py：因 gbbq 禁令显式 skip，不伪装通过。
- reports/price_only_validation_v1/{PRICE_ONLY_VALIDATION_V1.md,INVENTORY_CORRECTIONS_V1.json,PRICE_ONLY_CAPABILITY_MATRIX_V1.json,REGRESSION_RECONCILIATION_V1.json}、reports/junit-price-only-v1.xml：交付文档、十项更正、能力矩阵、回归对账与脱敏 JUnit。
- .github/workflows/price-only-indicator-validation-v1.yml：双平台 CI。
- 回滚点 f5acb0317719a42eb7071504048e341b50a7ac12；可 git revert 本分支提交，不影响 main 与旧工作区。

### Testing（PR #16 定点收尾追加）
- PR16-01：以合成哨兵复现五类绕过（相对/绝对不一致、.. 遍历、符号链接别名、显式清单被 basename 白名单早退、Windows 扩展路径）；修复为基于可信组合根的绝对身份判定，覆盖实际入口 TdxData._load_gbbq，open 探针证明拒绝先于打开；新增 19 项身份绕过测试。
- PR16-02：模块级无条件 skip 改为工作区级任务激活条件（离开本任务语义不变、环境变量不能放开真实数据）；合成可证 QFQ 性质拆出并实际执行 4 项，真实集成标 real_data_integration 并在任务激活时 skip 2 项；未删断言、未扩大 skip、未吞异常。
- PR16-03：修复 naive_wilder_rma 跨缺口继承旧段状态（[1,1,NaN,10,10,10] window=2 由 [NaN,2,NaN,11,15.5,17.75] 改为 [NaN,2,NaN,NaN,20,20]）；补 NATR/Keltner 缺口等值断言与无缺口正对照；证据口径由 230/20 修正为 226（162+41+8+19+4+11）。
- 目标套件 517 项通过、3 项分类 skip；完整套件零新增失败 nodeid（基线 194 覆盖本次 193）；5 项 skip 已逐项分类；因果未确认，不用数量相同冒充零回归。
- 双平台 CI 与 SonarCloud 全部通过（ubuntu 与 windows 各两次运行）。
### Notes
- src/chanlun_trader/price_only_scope.py：身份判定重写（解析 . / .. / 符号链接 / Windows 扩展前缀；清单先于白名单；无法解析 fail closed）。
- tests/price_only_scope/task_scope.py：任务激活条件（工作区级标记，非环境变量）。
- tests/price_only_scope/test_gbbq_identity_bypass_v1.py：19 项绕过测试（含 open 探针与实际入口）。
- tests/pit/test_qfq_pit_synthetic_v1.py：4 项合成可证 QFQ 性质（实际执行）。
- tests/pit/test_qfq_pit_safety.py：改为 real_data_integration 分类 + 任务激活 skip。
- tests/indicators_v2/_oracle_price_only_v1.py：naive_wilder_rma 按段独立播种。
- tests/indicators_v2/test_price_only_formula_increment_v1.py：补 RMA 缺口反例与 NATR/Keltner 缺口等值断言（162 项）。
- pyproject.toml：注册 real_data_integration 标记。
- reports/price_only_validation_v1/{PRICE_ONLY_CAPABILITY_MATRIX_V1.json,REGRESSION_RECONCILIATION_V1.json}、reports/junit-price-only-v1.xml：三项关闭矩阵、逐 nodeid 对账与脱敏 JUnit。
- 回滚点 3bcd5f7dd38519d83f0d3551575d888c8cf28ba7；可 git revert 本次提交。

### Testing（PR #16 残留收尾追加）
- PR16-01：复现 cwd 反例（相对清单 + 同一绝对路径，cwd 变化使判定由拒绝翻转为放行）；改为在组合根一次性解析并冻结禁止身份，检查时不读 cwd；环境变量至多追加约束、真实 gbbq 根身份始终保留；无法解析则 fail closed。新增 10 项作用域与冻结身份测试。
- PR16-02：改用显式激活（tests/conftest.py 组合根），生产 _load_gbbq 与真实测试分类消费同一 task_scope_active()；报告文件存在性不再作为激活开关；退出恢复原有权限。junction 改为 Windows 临时目录原生 mklink /J 实际创建并检查入口拦截（本机实际执行通过）；POSIX 标 NOT_APPLICABLE。
- PR16-03：新增 scripts/emit_price_only_evidence_v1.py，从实际 collect-only 与 JUnit 自动派生计数；修正 226 -> 249（六部分 245 + 合成 QFQ 4）；errors 与 failures 分开、skipped 不计 passed；移除 PENDING_COMMIT 占位；25 项逐行证据含证明强度分级；完整套件 JUnit 含本机路径，加入 .gitignore 不推送。
- 目标套件 528 项通过、2 项分类 skip；完整套件失败集合与基线逐条完全一致（各 194 含 errors），零新增零消除；因果未确认。
- 双平台 CI 与 SonarCloud 全部通过。
### Notes
- src/chanlun_trader/price_only_scope.py：冻结禁止身份（_build_frozen_denylist / rebuild_frozen_denylist / frozen_denylist）；显式任务作用域（activate_task_scope / deactivate_task_scope / task_scope_active）。
- src/chanlun_trader/tdx_data.py：_load_gbbq 按作用域启用守卫。
- tests/conftest.py：组合根显式激活任务作用域。
- tests/price_only_scope/test_task_scope_v1.py：10 项作用域/冻结身份/不串扰测试。
- tests/price_only_scope/test_gbbq_identity_bypass_v1.py：junction 原生创建测试；清单测试适配冻结入口。
- tests/pit/test_qfq_pit_safety.py、tests/test_tdx_data.py：统一消费 task_scope_active()。
- scripts/emit_price_only_evidence_v1.py：自动计数与逐项证据生成。
- reports/price_only_validation_v1/{EVIDENCE_COUNTS_V1.json,PRICE_ONLY_CAPABILITY_MATRIX_V1.json}、reports/junit-price-only-v1.xml：计数证据、残留关闭矩阵、脱敏 JUnit。
- .gitignore：排除含本机路径的完整套件 JUnit。
- 回滚点 8f94f503816282d0a05949c807d4ad63d0e7ab06；可 git revert 本次提交。

### Testing（PR #16 运行时政策与证据收尾）
- 身份两方向：在 C 盘 NTFS 临时目录原生 mklink /J 复现"清单列别名、访问目标本体"放行；修复为冻结身份闭包（realpath + 父目录解析），两方向一致拒绝；保留 cwd/绝对/相对/.. /basename 正反例。
- 外层任务保护：修复 _restore_scope 无条件 deactivate 缺陷；新增验收序列（外层激活 → 受保护缓存拒绝 → 内部清理 → 仍拒绝）与"外层未激活时正常/异常退出均恢复"测试。
- vendor 入口：新增 test_vendor_entry_guard_v1.py，用受保护合成目标驱动真实 GbbqReader 与 TdxData，open 探针证明拒绝先于打开且 vendor get_df 未被调用；junction 在 NTFS 原生创建并验证 opened=[]；POSIX 标 NOT_APPLICABLE；硬链接如实列限制。
- 证据：计数纳入新增文件，由实际 collection 派生为 262 + 合成 QFQ 4 = 266；收集失败抛 CollectionError 并让 main 返回非零；JUnit 缺失标 NOT_ESTABLISHED；新增证据回归测试（删映射/失败状态降级、nodeid 实存校验）。
- 目标套件 542 项通过、2 项分类 skip；完整套件零新增失败 nodeid（基线 194 覆盖本次 193）；因果未确认。
- 双平台 CI 与 SonarCloud 全部通过。
### Notes
- src/chanlun_trader/price_only_scope.py：冻结身份闭包（含 realpath 与父目录解析），两方向一致拒绝。
- tests/price_only_scope/test_task_scope_v1.py：保存/恢复进入前政策；外层任务保护验收序列（13 项）。
- tests/price_only_scope/test_vendor_entry_guard_v1.py：vendor 入口拒绝证据与 junction 两方向（4 项）。
- tests/price_only_scope/test_evidence_regression_v1.py：证据回归（7 项）。
- scripts/emit_price_only_evidence_v1.py：纳入全部新增文件；CollectionError；NOT_ESTABLISHED；逐项精确 nodeid 与证明强度。
- reports/price_only_validation_v1/{EVIDENCE_COUNTS_V1.json,PRICE_ONLY_CAPABILITY_MATRIX_V1.json}、reports/junit-price-only-v1.xml：派生计数、运行时关闭矩阵、脱敏 JUnit。
- 回滚点 b63cb1b6f843cfc54306602c0756d2b4a1c994d4；可 git revert 本次提交。

## 2026-09-23 - Task: PR #16 合并前定点收尾（作用域恢复、受控 reader、证据推导）
### What was done
在 PR #16 原分支完成三项残留的代码、测试与证据收尾。任务作用域新增完整政策快照与恢复，修复了第二个 fixture（vendor 入口测试）在 finally 中无条件 deactivate 会关闭外层任务的同类缺陷，并补 pytest 顺序回归。gbbq 受控入口拆分为间接（TdxData）与直接（受控 wrapper）两条路径各自作证，新增静态检查发现并整改了两处绕过 wrapper 的直接调用点；裸 vendor 调用如实标为不支持。证据生成器原地改造为按 JUnit testcase outcome 推导状态，参数化按最差聚合，并补输入变异测试。过程中修复了 CI 暴露的三个真实缺陷。
### Testing
- 目标套件 558 passed / 2 skipped；price_only_scope 82 passed。
- 作用域：外层激活→受保护合成缓存拒绝→内部清理→仍拒绝；pytest 顺序回归（子会话内两用例顺序执行）；正常/异常退出恢复；原未激活保持原状。
- 受控 reader：路径 A 间接（open 探针 + vendor get_df 未执行）；路径 B 直接（open/decode 前拒绝）；正对照（未受保护输入确实到达 vendor）。
- 证据变异：删映射、改 failure/error/skip、删 testcase、混合参数、缺失/损坏 JUnit 均降级；无关变异不误伤；空 JUnit 全降级。
- CI 暴露并修复的真实缺陷：①脱敏用 `<workspace>` 破坏 XML；②node_diff_vs_base 引用未定义常量 NOT_ESTABLISHED；③Sonar S8707 路径穿越（--junit/--out 直接当路径）+ 盘符路径跨平台语义差异。
- 完整套件零新增失败 nodeid（基线 194 覆盖本次 193）；因果未确认。
- 双平台 CI 与 SonarCloud 全部通过。
### Notes
- src/chanlun_trader/price_only_scope.py：新增 snapshot/restore_task_scope（含 reason、冻结禁止集合、环境清单）、controlled_gbbq_reader 与 CONTROLLED_READER_POLICY；移除重复的 is_forbidden_gbbq_path 定义。
- src/chanlun_trader/tdx_data.py：改用受控 wrapper；清理因本轮改动产生的未用导入。
- src/chanlun_trader/data/tdx/owner_export_v1.py：直接 vendor 调用改经受控 wrapper。
- scripts/emit_price_only_evidence_v1.py：状态由 JUnit outcome 推导、参数化最差聚合、resolve_within_repo 路径限制、--junit/--out 参数、node_diff_vs_base。
- tests/price_only_scope/test_task_scope_v1.py：保存/恢复完整政策、外层任务保护、monkeypatch 化。
- tests/price_only_scope/test_vendor_entry_guard_v1.py：路径 A/B 分开作证、正对照、junction 两方向、monkeypatch 化。
- tests/price_only_scope/test_controlled_reader_boundary_v1.py（新增）：AST 静态检查防调用点绕过。
- tests/price_only_scope/test_scope_order_regression_v1.py（新增）：完整 pytest 顺序回归。
- tests/price_only_scope/test_evidence_mutation_v1.py（新增）：输入变异测试与路径穿越回归。
- tests/price_only_scope/test_evidence_regression_v1.py（删除）：其断言方式正是本轮禁止的模式，已被变异测试取代。
- .github/workflows/price-only-indicator-validation-v1.yml：新增用本次 CI 实际 JUnit 运行生成器的步骤。
- reports/price_only_validation_v1/{EVIDENCE_COUNTS_V1.json,PRICE_ONLY_CAPABILITY_MATRIX_V1.json}、reports/junit-price-only-v1.xml：派生证据、关闭矩阵、脱敏 JUnit。
- 回滚点 1b03623b39f08ef4f947b72e24f714897aaca308；可 git revert 本轮提交。

### Testing（补充：A1 重读门控与边界自查）
- 边界自查发现：审计日志累计 73 条真实数据根读取（E:\new_tdx_mock\vipdoc，全部在 A1 授权窗口 20240102-20240731，未触碰封存期），原因是目标套件包含真实样本测试且本轮多次运行套件。
- 已用任务作用域门控：test_real_raw_sample_v1.py 标 real_data_integration，任务激活时显式 skip。
- 验证：运行前后真实数据根读取计数均为 73，新增 0；目标套件 547 passed / 13 skipped。
- 历史 73 条读取如实保留在审计日志，不抹除。
### Notes
- tests/price_only_scope/test_real_raw_sample_v1.py：加 real_data_integration 标记与任务作用域门控，停止重复真实采样。
- reports/price_only_validation_v1/EVIDENCE_COUNTS_V1.json、reports/junit-price-only-v1.xml：按门控后状态重生成并脱敏。
- 回滚点 f45488c07a7195f8323d25f9ec97c80988ad5aa7。

## 2026-09-23 - Task: PR #16 相对链接绑定、证据逐项适用、顺序回归接真实 fixture
### What was done
修复相对符号链接按 cwd 而非组合根解析的绕过；把证据生成器从"函数全体参数替单项作证"改为逐项适用，结果诚实收缩；把顺序回归接到仓库真实 fixture 并补防退化变异。过程中修复 Sonar 阻断的 5 个 CRITICAL 与 1 个 BUG。
### Testing
- 相对链接：复现 cwd != 组合根时目标本体可读；修复后 cwd 等于/不同于组合根、冻结后改 cwd 三种情况判定一致。新增 9 项交叉覆盖（绝对/相对 × 列别名/列目标 × cwd 位置），受保护情况经受控 wrapper 与 TdxData 入口验证 open=0，允许夹具保留正对照。
- 证据逐项适用：复现条件套件实际只覆盖 11 个指标而生成器签 25 项；修复后 11 VERIFIED / 14 PARTIAL。17 项变异测试（删 DEMA 条件/账户节点、仅留单参数、账户 failure/error/skip、缺期望节点、错指标节点、缺/损坏 JUnit、有效输入正对照、CLI 穿越）。
- 顺序回归：改为加载真实 _restore_scope 并驱动 setup/teardown；下一测试经受控 wrapper 验证 opened=[]；变异用例证明坏 fixture 下回归会失败。
- Sonar：S1192×2、S3776×3、S4143（重复字典赋值 BUG）全部修复；质量门通过。
- 目标套件 558 passed / 13 skipped；完整套件零新增失败 nodeid（基线 194 覆盖本次 193），因果 UNCONFIRMED；真实读取新增 0。
- 双平台 CI 与 SonarCloud 全部通过。
### Notes
- src/chanlun_trader/price_only_scope.py：新增 _anchored_path/_resolve_real_paths 统一以组合根为解析基准；拆出 _split_drive/_fold_segments/_matches_root_basename/_windows_form_normalised 降低复杂度。
- scripts/emit_price_only_evidence_v1.py：新增 build_expected_coverage/collect_parameterized_nodeids；resolve_dimension 改为按期望覆盖判定；拆出 _case_outcome/_merge_worst；提取 CONDITION_MODULE/FORMULA_MODULE；删除重复字典赋值；加 CLI --junit/--out 与路径限制。
- tests/price_only_scope/test_relative_link_binding_v1.py（新增）：9 项相对链接交叉覆盖。
- tests/price_only_scope/test_evidence_mutation_v1.py：重写为复核指定的 5 类输入变异。
- tests/price_only_scope/test_scope_order_regression_v1.py：改为加载真实 fixture + 防退化变异。
- reports/price_only_validation_v1/{EVIDENCE_COUNTS_V1.json,PRICE_ONLY_CAPABILITY_MATRIX_V1.json}、reports/junit-price-only-v1.xml：逐项证据、关闭矩阵、脱敏 JUnit。
- 回滚点 8e4e20386b4970d1bfca7e35a1d1ae53acee0380。

## 2026-09-23 - Task: PR #16 读入身份绑定与证据适用性收尾
### What was done
修复守卫与实际打开使用不同文件身份的绕过（相对 cache_dir 被按组合根解释、读取却按 cwd 打开）；把证据生成器的适用性剩余补齐：映射既有单独条件测试、区分注册输出与已验证输出、计数补入新文件。
### Testing
- 读入身份：复现组合根 T/source、cwd T/outside、清单列绝对哨兵时，cache_dir 用相对写法会真实读出同一哨兵；修复后守卫/exists/读取消费同一已解析绝对对象。新增 9 项交叉覆盖（绝对/相对 cache_dir × cwd 位置 × 冻结后变 cwd），open 探针证明拒绝先于打开，受控 reader 绑定验证，允许合成输入正对照。
- 证据适用性：CCI/NATR/PSY 的既有单独测试补入映射（原被误标"无覆盖"）；DEMA 注册 3 输出仅 dema 被断言，新增 validated_outputs/tested_params 与 registered_* 分列。24 项变异测试全部通过。
- 目标套件 574 passed / 13 skipped；完整套件零新增失败 nodeid（基线 194 覆盖本次 193），因果 UNCONFIRMED。
- 真实数据读取：运行前后计数均为 73，新增 0；用合成哨兵验证开关，未读取真实文件。
- 双平台 CI 与 SonarCloud 全部通过。
### Notes
- src/chanlun_trader/price_only_scope.py：新增 resolve_input_path()（唯一输入解析边界，相对项按组合根锚定）；controlled_gbbq_reader 绑定守卫与 vendor 打开同一对象。
- src/chanlun_trader/tdx_data.py：新增 _resolve_cache_dir()；__init__ 解析 cache_dir 为绝对；_load_gbbq 的守卫、exists、读取共用同一对象。
- scripts/emit_price_only_evidence_v1.py：新增 SINGLE_CONDITION_TESTS 与 VALIDATED_BY_DIMENSION；行内区分 registered_* 与 validated_outputs/tested_params；计数补入两个新测试文件。
- tests/price_only_scope/test_read_path_identity_v1.py（新增）：9 项读入身份交叉覆盖。
- tests/price_only_scope/test_evidence_mutation_v1.py：补 7 项适用性变异。
- tests/price_only_scope/test_scope_order_regression_v1.py：措辞改为准确说明手动驱动 generator fixture。
- reports/price_only_validation_v1/{EVIDENCE_COUNTS_V1.json,PRICE_ONLY_CAPABILITY_MATRIX_V1.json}、reports/junit-price-only-v1.xml：逐项证据、关闭矩阵、脱敏 JUnit。
- 回滚点 40e18ab3a53242bd4ef702f2883318d91d6aa16f。

## 2026-09-23 - Task: PR #16 证据适用范围修正（声明不得超过实际断言）
### What was done
逐项核对本 PR 25 项指标的证据映射，把声明收窄到实际测试断言范围；参数证据从"参数名"改为"实际取值与组合"；补最小输入变异回归。
### Testing
- 核对方法：从测试源码 AST 提取每个指标对应测试函数中所有 .value("<output>") 调用与 @pytest.mark.parametrize 实际取值。
- 全量核对发现 5 项声明超过/偏离断言：DONCHIAN（middle 未断言）、KELTNER（middle/atr 未断言）、ROLLING_VOLATILITY（return 未断言）、MACD_HIST_RAW（dif/dea 未断言）、DRAWDOWN_FROM_PEAK（peak 实际已断言，原先漏声明，已补正）。
- 每行新增 unvalidated_registered_outputs，保证 validated ∪ unvalidated = registered。
- 参数证据改为 tested_parameter_sets：DEMA=[{window:5},{window:20}]、MACD_HIST_RAW=[{fast:12,slow:26,signal:9}]、KELTNER=[{window:20,atr_window:10}]；条件维度记录注入阈值。不从注册默认值推导范围。
- 新增 7 项变异测试（合计 31 项）：声明不得超断言、已知四项修正、参数须为取值字典、注册默认值不得扩大范围、兄弟输出 passed 不得附带认证、删 testcase 只降级该范围、合格主输出正对照。
- 目标套件 581 passed / 13 skipped；完整套件失败集合与基线逐条完全一致（各 194 含 errors），零新增零消除，因果 UNCONFIRMED。
- 真实数据读取：运行前后计数均为 73，新增 0。
- 双平台 CI 与 SonarCloud 全部通过。
### Notes
- scripts/emit_price_only_evidence_v1.py：VALIDATED_BY_DIMENSION 逐项改为实际断言的输出与取值；tested_params 改为 tested_parameter_sets；新增 unvalidated_registered_outputs。
- tests/price_only_scope/test_evidence_mutation_v1.py：补 7 项证据范围变异；修正既有测试对新字段的引用。
- reports/price_only_validation_v1/EVIDENCE_COUNTS_V1.json、reports/junit-price-only-v1.xml：重新生成并脱敏。
- 回滚点 d2e91789ed861200fae5eaadb86d2a063bf775e5。

## 2026-09-24 - Task: PR #16 合并前全链路复核与修复
### What was done
独立检查 PR #16 的指标计算、访问边界、pytest 任务作用域、证据生成器及正式报告；修复无效行跨段比较、KELTNER 预热、TRIX 信号线、负成交额/无效 OHLC 的 ready、OWNER gbbq 读取前守卫与有界 `.day` 请求起点越界。证据仅列本次 JUnit 中通过的参数节点，账户参数从测试源码取值；未绑定当前源码的旧完整套件及节点差集标为未建立。PR 保持 Draft，未合并。
### Testing
- 本机 Windows Python 3.13.5：A0 公式/条件 291 passed；访问边界全集 98 passed / 23 分类 skipped；账户入口 37 passed；既有 V1/V2 回归 152 passed / 2 分类 skipped；受影响 OWNER 合成测试 12 passed。
- 证据变异测试 33 项通过（包含删参数节点、空 JUnit、实际 JUnit 身份及无身份节点清单）；目标新测试仅使用合成数据。`compileall`、JSON 语法与 `git diff --check` 通过。
- 本轮 A1 真实 RAW 样本 11 项按任务作用域分类跳过；未重新读取真实样本。当前源码完整套件未执行，因此没有当前 HEAD 的全量失败集合对账；旧对账仅保留为历史原件。
### Notes
- `src/chanlun_trader/engine/indicators_v2.py` 与 `tests/indicators_v2/`：指标计算及独立 oracle/边界回归。
- `src/chanlun_trader/data/tdx/owner_export_v1.py`、`src/chanlun_trader/research/io_safety.py` 与 `tests/price_only_scope/`：读取前守卫、有界读取及合成哨兵。
- `tests/conftest.py`、`.github/workflows/price-only-indicator-validation-v1.yml`：price-only 作用域改为显式 CI 选用。
- `scripts/emit_price_only_evidence_v1.py`、`reports/price_only_validation_v1/`、`reports/junit-price-only-v1.xml`：证据范围、计数及历史状态；`progress.md` 与报告说明记录本轮结果。
- 合并前复核补充：普通 pytest 关闭任务作用域后，A1 样本测试原本会在真实文件存在时重新读取；现改为默认跳过，仅未来另行明确授权且设置 `CHANLUN_RUN_A1_RAW_SAMPLE=1` 才执行。证据参数匹配同时覆盖 `[20]` 与 `[20-形状]` 两种 pytest 节点格式；生成器逐项核对 25 项公式测试源码中进入逐值断言的输出与参数组合，漂移时拒绝签发。
- 本轮补充验证：证据变异 34 passed；普通 pytest 且未设置 A1 开关时 11 skipped，新增真实读取 0。完整本机套件与当前 SHA 的远端 CI 状态尚未建立，不把既有基线结果冒充当前结论。
- 源码提交 `0ea0dcd` 后重跑 A0 目标套件 291 passed、完整 price_only_scope 99 passed / 23 skipped；据新 JUnit 重生成证据，新增测试子集 333 项、指标 14 VERIFIED / 11 PARTIAL，JUnit SHA256 与报告一致。
- 回滚点 `04bb3a25d634b4362b549a1a7d3e8332b907e666`；可对本轮提交按逆序执行 `git revert`，不需改动旧工作区。

## 2026-09-24 - Task: PR #16 独立复核的三项阻塞修复
### What was done
按 P1/P2 复核意见修复：gbbq 保护测试改为自行保存、激活、恢复任务作用域并只使用合成哨兵；`TRIX_V1` 的 `trix_ma` 恢复公开契约的算术滚动均值，修正 oracle 和公开入口回归；目标 pytest JUnit 写入运行时源码树指纹，生成器核对身份后才签发 VERIFIED。同步更新正式报告、能力矩阵及 `docs/PRICE_ONLY_VALIDATION_RUNBOOK_V1.md`。PR 保持 Draft，未合并或部署。
### Testing
- 普通 pytest 下两个 gbbq 模块 25 passed / 2 skipped；没有真实 gbbq 或项目缓存读取。
- 已提交源码 `168f2343b5dac90bc51a68704110585cb2cc1c05` 上：A0 公式/条件 291 passed；账户入口链 37 passed；既有 V1/V2 回归 152 passed / 2 skipped；完整 price-only 访问与证据套件 100 passed / 23 skipped，其中 11 项 A1 默认跳过。
- 重新生成的 `reports/junit-price-only-v1.xml` 记录 `COMMITTED_SOURCE` 和源码树指纹；`EVIDENCE_COUNTS_V1.json` 的 JUnit 哈希相符、`junit_source_binding=MATCHED`，25 项为 14 VERIFIED / 11 PARTIAL，新增文件子集 334 项。证据变异测试共 35 项，旧或缺身份 JUnit 会降级。
- `PRICE_ONLY_CAPABILITY_MATRIX_V1.json` 解析与 `git diff --check` 通过；本轮未读取或重跑 A1 真实 RAW。
### Notes
- 源码及测试：`src/chanlun_trader/engine/indicators_v2.py`、`scripts/emit_price_only_evidence_v1.py`、`tests/conftest.py`、`tests/indicators_v2/`、`tests/price_only_scope/`。
- 正式证据及说明：`reports/junit-price-only-v1.xml`、`reports/price_only_validation_v1/`、`docs/PRICE_ONLY_VALIDATION_RUNBOOK_V1.md`、本文件。
- 当前源码完整套件未重跑；A1 PASS 仅为历史记录。以本轮开始前的 `eef298075d16c0b571f5429f6e95dcc2735a2512` 为回滚点，按逆序 `git revert` 本轮提交即可撤回，不触及旧工作区。

## 2026-09-25 - Task: PR #16 重复 JUnit testcase 的证据误认证修复
### What was done
根据独立复核的 P2 复现，`parse_junit_outcomes` 对同一精确 nodeid 改用既有最差 outcome 合并逻辑；去参数基名仍按最差结果聚合。增加失败记录在通过记录之前和之后的两项回归。重新生成目标 JUnit 与 25 项证据，更新正式报告、能力矩阵和证据生成说明。PR 保持 Draft，未合并或部署。
### Testing
- 合成重复 JUnit 的两种排列均通过真实解析器与 `bound_indicator_evidence`：精确节点及基名为 failed，DEMA 条件和综合状态降为 PARTIAL，TEMA 正对照保持 VERIFIED；2 passed。
- 已提交源码 `d6a4fd41ad26e35909b262b1baf82dac2e1a40ae` 上 A0 公式/条件 291 passed；完整 price-only 套件 102 passed / 23 skipped，其中 11 项 A1 默认跳过，另 12 项为本机链接能力限制。
- 重新生成 `reports/junit-price-only-v1.xml` 与 `EVIDENCE_COUNTS_V1.json`；运行时源码身份 `MATCHED`、JUnit SHA256 相符、25 项仍为 14 VERIFIED / 11 PARTIAL，新增文件子集 336 项，证据变异测试共 37 项。
- 本轮未读取或重跑 A1 真实 RAW/gbbq；当前源码完整仓库套件未重跑。
### Notes
- `scripts/emit_price_only_evidence_v1.py`：重复精确 nodeid 按最差结果合并；`tests/price_only_scope/test_evidence_mutation_v1.py`：两种排列的合成负向回归。
- `reports/junit-price-only-v1.xml`、`reports/price_only_validation_v1/`、`docs/PRICE_ONLY_VALIDATION_RUNBOOK_V1.md`：新源码身份的正式证据、计数与适用范围；本文件记录验证和回滚。
- 以本轮开始前的 `a3d8deb742aa42d1189a51509b62afd520188fd7` 为回滚点，按逆序 `git revert` 本轮提交即可撤回，不触及旧工作区。

## 2026-09-25 - Task: 51 指标固定策略真实数据首次试验

### What was done
- 在隔离工作树 `origin/main@69b7d9f` 上新增 `scripts/probe_all_indicator_strategy_v1.py`：冻结 51 个指标的版本、输出和公式指纹，定义每指标一票的固定买卖规则，并通过受控日期读取做输入预检；少一项即不产出判断或账户结果。
- 使用现有 BaoStock 日线快照对 `000001.SZ` 和 `600000.SH` 各 200 根日线实算；每只股票 50/51 项可计算，`TURNOVER_RATE` 因缺历史流通股本阻塞。正式输出见 `reports/all_indicator_fixed_strategy_pilot_20260925/PROBE.json`。
- `docs/ALL_INDICATOR_PILOT_V1.md` 说明固定规则、证据分层、数据阻塞、复验命令与继续账户核对所需输入；`tests/indicators_v2/test_all_indicator_pilot_v1.py` 验证全指标参与和缺项拒绝。

### Testing
- `python -m pytest -q -p no:cacheprovider tests/indicators_v2/test_all_indicator_pilot_v1.py`：3 passed；pytest 退出时仍出现既有临时目录权限警告，进程退出码为 0。
- 真实数据探针按预期返回 `BLOCKED`：审计记录日期下推读取 1,020,151 行，最高日期 2024-07-31；两只样本各 50 项在样本末根已就绪，唯一阻塞为 `DATA_DEPENDENCY_NOT_MET:TURNOVER_RATE:float_shares`。报告 JSON 解析及 51 个唯一 ID 一致性复查通过。
- BaoStock 补查 `turn` 的单次限定窗口登录约 10 秒后超时，没有取得新增行情。本次未运行真实交易、账户或统计验证。

### Notes
- 改动文件仅为上述脚本、测试、文档、报告与本条记录；未改业务引擎、数据或原研究产物。此工作树与原有未提交工作区隔离。
- 回滚点为 `69b7d9fc234bbde876594ad7a7bc9c72dab81b53`；删除本次新建的四个文件并恢复本工作树的 `progress.md` 到该提交即可撤回。不要对原工作区执行此回滚。

## 2026-09-25 - Task: BaoStock 历史换手率接入并跑通 51 指标固定策略链路

### What was done
- 在试验专用注册表为 `TURNOVER_RATE` 增加显式版本 `TURNOVER_RATE_BAOSTOCK_TURN_V1`，直接使用 BaoStock 日线百分比 `turn`；原有通用 `TURNOVER_RATE_V1` 和默认注册表不变。固定策略冻结 51 个指标各一票，不以缺项替代运行。
- `scripts/fetch_all_indicator_turn_v1.py` 冻结两只样本证券 2023-10-09 至 2024-07-31 的 400 条换手率和来源哈希；`scripts/fetch_all_indicator_actions_v1.py` 冻结 2024-02-01 至 2024-05-31 的 BaoStock 分红/送股窗口筛查。`scripts/probe_all_indicator_strategy_v1.py` 验证原始日线、逐日换手率及成交量、历史状态和公司行动后，提交固定买卖信号给既有事件引擎，导出订单、成交、费用、每日账户和独立复算。
- 更新 `tests/indicators_v2/test_all_indicator_pilot_v1.py`、`docs/ALL_INDICATOR_PILOT_V1.md`，将报告 `reports/all_indicator_fixed_strategy_pilot_20260925/PROBE.json` 从首轮阻塞诊断更新为完整链路结果。此前 progress 条目记录的是首次受缺流通股本阻塞的历史状态；该同名报告现已由本轮结果取代。

### Testing
- 目标测试 4 passed，包含全指标临界票数参与、缺项拒绝、供应商换手率原值、合成端到端账户及成交量/公司行动/账户快照篡改负例；未跑完整仓库套件。
- 真实 BaoStock 样本两只证券各 200 日、51/51 指标可计算；306 次分日截断重算与完整序列对应值一致。限定无公司行动窗口内 76 个交易日产生 152 信号、74 订单、68 成交；逐日独立现金、持股和资产复算最大差异 0，账本不变量和订单/成交数量、下一开盘参考价、费用检查通过。
- 相同快照、源码和命令连续两次生成完全相同的报告 SHA-256：`5ddbf1ccc2712d19a6963dd292ebb468579ddabf3a40be5fbcea4ff0d84a0b79`。`blockers=[]`、`account_status=RECONCILED_DIAGNOSTIC`。受控物理读取最高日期 2024-07-31，未触及封存测试区间。

### Notes
- 所有实现和数据快照仅位于隔离工作树；未改既有引擎、默认指标或用户原工作区。新增/更新文件为上述 3 个脚本、1 个测试、1 个文档、`reports/all_indicator_fixed_strategy_pilot_20260925/` 内 4 个正式证据文件及本条记录。
- +3.80% 仅为两只银行股、76 日模拟账户诊断观察；历史 `turn` 发布时间、独立公司行动覆盖、引擎 PIT 历史资格接线、样本外统计和 Paper 资格尚未通过。该固定投票规则交易频繁、指标经济方向混杂，不能据此认定策略有效。
- 回滚整个隔离试验的基线为 `69b7d9fc234bbde876594ad7a7bc9c72dab81b53`：仅在此工作树删除试验新增脚本、测试、文档和报告文件，并把 `progress.md` 恢复到该提交；原工作区不受影响。若只撤销本轮，请依据本条列出的新增快照和本轮差异逆向恢复首轮试验文件，不覆盖用户其他未提交改动。

## 2026-09-25 - Task: 固定策略诊断链路进入分支与主分支 CI

### What was done
- 为 `.github/workflows/price-only-indicator-validation-v1.yml` 增加 `main` 推送触发，使本次 PR 验收后，合并提交也能运行相关指标验收。试验文档明确 GitHub CI 只覆盖合成回归；本地 E 盘真实行情与状态不在 CI 内。

### Testing
- 本地按 CI 的 Windows `PYTHONPATH` 运行 `tests/indicators_v2 tests/conditions_v2`：295 passed；暂不把远端 PR 或 `main` CI 记为已通过，远端执行状态以 GitHub Checks 为准。
- 暂存文件 `git diff --cached --check` 无空白错误；公司行动与换手率快照的冻结哈希随报告提交。

### Notes
- 新增改动文件为上述工作流、试验文档及本记录。11 KB 的两证券换手率冻结快照是刻意纳入版本管理的试验证据；仓库一般忽略 `*.parquet`，本次仅对该明确文件单独暂存。
- 如需撤销主分支 CI 触发，删除工作流 `push.branches` 中的 `main` 并通过新 PR 回滚；不影响其它工作流。试验整体仍可以 `69b7d9fc234bbde876594ad7a7bc9c72dab81b53` 为基线在隔离工作树回退。

## 2026-09-25 - Task: PR #17 路径边界与 SonarCloud 安全告警修复

### What was done
- PR 首轮 SonarCloud 报告脚本路径穿越：将真实试验输入限制在工作树与指定 E 盘数据目录，文件哈希也执行同一边界检查；固定报告输出仅允许 `reports/all_indicator_fixed_strategy_pilot_20260925/PROBE.json`。合成测试显式声明临时夹根目录，正式 CLI 不接受此例外。
- 更新目标测试和试验说明；重新生成该报告，将本轮脚本源码指纹写入报告。前一条记录中的报告 SHA-256 属于修复前运行，当前报告以本条哈希为准。

### Testing
- 目标测试 5 passed；按 CI Windows 路径运行 `tests/indicators_v2 tests/conditions_v2`：296 passed。越界文件拒绝、原有负例与真实 51/51 指标链路均通过。
- 相同输入在当前检出状态连续两次生成相同报告 SHA-256 `416cc20b9bd77c685138164bf62c7bff4162f2ec00cd6d61078bc534951175f1`；`blockers=[]`、76 日账户逐日复算差异 0。远端 SonarCloud 对本次修复的复检以更新后的 PR 检查结果为准。

### Notes
- 本次修改为 `scripts/probe_all_indicator_strategy_v1.py`、对应测试、说明文档和报告；未扩大真实数据日期范围。回滚本轮时可在隔离分支上对本轮后续提交执行 `git revert`，保留此前固定策略试验提交；如仅撤销路径限制，须同步恢复测试与报告源码指纹。

## 2026-09-25 - Task: 固定 51 指标策略扩大至真实公司行动窗口并核对模型账户

### What was done
- 在独立分支 `codex/s1-trusted-baseline` 冻结 2024-02-01 至 2024-07-31 的两证券账户范围；`docs/S1_FIXED_STRATEGY_BASELINE_V1.md` 区分模型诊断、S1 验收和策略有效性。
- `src/chanlun_trader/research/io_safety.py` 增加 ISO 字符串日期 Parquet 的受控区间读取；`scripts/verify_fixed_strategy_state_v1.py` 对上游历史状态与账户状态逐日核对，约束前一日状态的建模可用时间，并在成交时接入现有历史交易状态门禁。
- `scripts/fetch_s1_actions_v1.py` 冻结 BaoStock 公司行动快照；`scripts/verify_s1_corporate_chain_v1.py` 对照发行人公告和原始查询记录，驱动 `src/chanlun_trader/engine/individual_dividend_accounting_v1.py` 按个人 A 股现金分红到账与卖出补税核算。`scripts/probe_all_indicator_strategy_v1.py` 扩展原链路，独立复算每日现金、持股、费用、T+1、分红及税，不改变 51 指标投票规则。
- 更新 `docs/DATA_ACCESS_POLICY.md` 和受影响测试；正式证据为 `reports/s1_trusted_baseline_20260925/BAOSTOCK_ACTIONS.json`、`STATE_GATE.json`、`CORPORATE_CHAIN.json`。

### Testing
- 相关回归 254 passed，邻接条件、退出、公共入口和 PIT 回归 159 passed；源码编译和 `git diff --check` 通过。合成用例覆盖 ST、停牌、缺交易状态、缺行情、零交易、公司行动快照篡改、非法 ISO 日期及未知状态标志，以及分红账本恢复后不重复到账与补税。
- 本机受控读取真实数据至 2024-07-31；76 日状态接线结果与原 68 笔成交及每日账户一致。扩大窗口为 118 个交易日、94 笔模拟成交、46 个无成交日，50 次独立逐 lot T+1 检查；逐日账户复算最大差异为零，重复执行的经济哈希一致。浦发银行 58,000 股分红入账 18,618 元、卖出补税 3,723.60 元，均与独立复算一致。

### Notes
- `STATE_GATE` 与 `CORPORATE_CHAIN` 仅标记 `PASSED_MODELED`；**S1 仍为 `NOT_PASSED`**。供应商历史发布时间、复权指标与原始成交价分离、公共策略入口一致性和整引擎中断恢复尚未证实；收益不构成策略有效性证据。个人账户税务模型不自动适用于其他投资者身份。
- 本轮修改文件为上述脚本、账本、读取器、两份文档、对应测试、三份报告和本记录。实现基线为 `4e3c2f1`；回滚时在本隔离分支撤销本轮提交或在合并后对对应提交执行 `git revert`，保留原试验与用户主工作区未提交改动。

## 2026-09-25 - Task: 固定 51 指标规则经公共策略入口逐日核对

### What was done
- 新增 `scripts/s1_public_entry_strategy_v1.py`：把已冻结的 51 指标等权投票规则实现为 `strategy_interface_v1.run` 可调用的本地策略插件；入口自行计算每项指标及每日投票，并在接入既有模型账户组件前检查买卖意图与前一日历史状态。规则与输入由计划和内容身份绑定。
- 新增 `scripts/verify_s1_public_entry_v1.py`：受控读取同一批真实数据，先核对原报告、源码和输入哈希，再经公共策略入口运行；逐日与 `PROBE.json` 的 280 条决策及 `CORPORATE_CHAIN.json` 的 118 日账户、106 个订单、94 笔成交和公司行动结果核对。证据写入 `reports/s1_trusted_baseline_20260925/PUBLIC_ENTRY_PARITY.json`。
- `tests/research_factory/test_s1_public_entry_v1.py` 覆盖每项指标可改变买卖、缺失/未来数据拒绝、合成数据公共入口及账户一致性；`.github/workflows/price-only-indicator-validation-v1.yml` 纳入此测试；更新 `docs/S1_FIXED_STRATEGY_BASELINE_V1.md` 的验收状态与复跑命令。

### Testing
- 本机真实快照受控复跑：`public_entry_step_status=PASSED_MODELED`，两证券各 140 条决策均与冻结报告一致；118 日逐日现金、持股、资产一致，94 笔成交与 106 个订单一致，最大账本复算差异 0，`blockers=[]`。新报告 SHA-256 为 `426064a7b66cfddb22c3eb02c4a4ca7011a71bdd16a8a58dc2c15d3552d448a1`；该文件含一次性诊断回执，因此全文件哈希不作为重复运行的确定性要求，经济结果哈希为 `6dc98531999cef81070725c0962ef18646d3006150c7b198c06e37ccbac66589`。
- 按 Windows CI 的 `PYTHONPATH` 与价格指标任务作用域运行相关指标、条件、退出、入口及策略接口回归：352 passed，1 条既有 Starlette 弃用警告；pytest 退出码为 0。GitHub 远端 CI 尚待推送后验证。

### Notes
- 此次证明固定参照规则通过公共 `run` 函数的同决策和同模型账户，不代表任意 AI 新策略自动接入，也不证明策略有效。BaoStock `turn` 与历史状态在当时的真实发布时间、分红前后指标复权口径、整引擎中断恢复仍缺证；S1 继续为 `NOT_PASSED`，不授予 Paper 资格。
- 本次新增两个脚本、一份测试、一份报告，修改工作流、S1 说明和本记录；报告不包含 E 盘原始行情文件。回滚时在隔离分支撤销本轮提交，或合并后对本轮提交执行 `git revert`；先前 `CORPORATE_CHAIN.json` 及用户原工作区的未提交改动不受影响。

## 2026-09-26 - Task: 固定 51 指标策略的历史可见性、除息口径与离线引擎中断恢复

### What was done
- `source_availability_v1.py` 对原始日线、BaoStock `turn`、历史证券状态要求逐行当时发布或同期采集时间；现有快照缺少该证据，严格历史资格明确为 `BLOCKED`，没有把建模 `available_at` 当成供应商发布时间。
- `causal_dividend_features_v1.py` 和 `s1_causal_price_strategy_v1.py` 保持 51 指标、等权投票、26 票阈值不变，只在已公告现金分红除息日起计算因果后复权指标；账户继续使用未复权成交和估值。
- `engine_replay_recovery_v1.py` 为无外部交易副作用的预置信号历史回测写入原子事件回执；重启时用同一输入和代码身份重放、核对中断前缀，再继续。`verify_s1_price_recovery_v1.py` 通过公共策略入口运行并对照账户，覆盖登记日、到账日和真实数据跨进程恢复；证据见 `reports/s1_trusted_baseline_20260925/PRICE_PIT_RESTART.json`。
- 更新 `docs/S1_FIXED_STRATEGY_BASELINE_V1.md`、新增 `docs/S1_PRICE_AVAILABILITY_RESTART_V1.md`，并把针对性测试纳入双平台 CI 工作流。

### Testing
- 本机真实 E 盘输入：两次现金分红的公告现金额与除息参考前收盘价相符；平安银行 2 个、浦发银行 3 个收盘判断相对旧未复权口径改变。118 个账户日、106 个订单、93 笔成交，独立账户核对无差异；7 月 17 日和 18 日中断重放以及 7 月 18 日真实退出进程后新进程恢复均与连续运行的成交、现金红利和补税一致。报告状态 `PASSED_MODELED`、严格历史资格 `BLOCKED`、S1 `NOT_PASSED`。
- 按现有 Windows CI 作用域执行指标、条件、退出、入口、PIT 和新恢复测试：596 passed、23 skipped、1 条既有 Starlette 弃用警告；pytest 退出码 0。另有退出时临时目录清理的 Windows 权限提示，不影响测试结果。`git diff --check` 与源码编译通过。GitHub CI 结果以推送后的检查为准。

### Notes
- 2024 年日线、换手率及历史状态实际发布时刻无法从 2026 年重拉快照中恢复；本轮没有伪造通过。下一步需找当年留存的可信发布时间，或从现在起形成同期采集证据，再开展前瞻观察。离线重放不证明券商接口或 Paper 外部订单幂等，也不证明固定策略有投资价值。
- 本轮新增三份研究/恢复模块、两份脚本、一份测试、一份报告和一份说明文档，修改 CI、S1 说明及本记录；未修改原始 E 盘数据和先前冻结报告。回滚时对本轮合并提交执行 `git revert`，保留既有 S1 参照报告及用户原工作区未提交改动。

## 2026-09-26 - Task: PR #20 恢复回执与真实数据验收脚本的路径边界

### What was done
- SonarCloud 首次检查指出恢复子进程可由输入包决定读取和写入路径。`engine_replay_recovery_v1.py` 现在要求调用方指定已存在的回执根目录，只允许该目录下一层 JSON 文件，解析路径后拒绝符号链接及目录穿越。
- `verify_s1_price_recovery_v1.py` 在哈希和读取之前，按项目既有白名单解析真实行情、换手率、状态、公告快照和历史状态路径；跨进程输入包限定在本次命名的系统临时目录，回执及结果文件名由程序固定，正式报告限定在项目内固定位置。更新路径拒绝测试并以受控路径重新生成真实报告。

### Testing
- 路径修复后目标测试 7 passed；新增临时目录外输入包和回执越界拒绝用例。真实 E 盘联合报告再次为 `PASSED_MODELED`，跨进程恢复的最终经济哈希与连续运行相同，严格历史资格仍为 `BLOCKED`；`git diff --check` 通过。远端 SonarCloud 与双平台 CI 以本次推送后的复检为准。

### Notes
- 这次只收紧恢复/验收文件边界，不更改固定策略投票或账户规则。修改文件为恢复模块、验收脚本、对应测试和重新生成的报告。可对本轮路径修复提交执行 `git revert` 回滚；若撤销整个三项验收功能，还需撤销前一提交，保留先前冻结报告。

## 2026-09-26 - Task: 规划固定策略验收之后的小规模自主研究闭环

### What was done
- 基于 `origin/main@ad82cbde01514bd6a07f01ac34742de8fd4b7b9d` 核对 AI 编排、候选物化、正式 caller、公共策略账户、失败反馈、统计原型、Paper 与组合预览的源码边界及对应测试。
- 新增 `docs/plans/2026-09-26-001-feat-bounded-autonomous-research-plan.md`：用业务架构图、能力表、四个近期实施单元和分阶段路线明确下一功能为有预算的真实研究闭环。按用户最新决定，历史发布时间取证不再阻塞近期功能；旧严格资格报告不变。
- 规划明确区分工程观察与策略资格，不把历史回放算成真实 Paper，不把 51 指标固定验收样本当作所有后续策略必须遵循的格式。

### Testing
- 本轮为规划交付，未执行测试、回测、AI 研究或实时采集。只读核对源码、测试定义和既有 S1 验收报告，并检查新增文档的流程、路径及差异；不将历史测试通过记录视为本轮新测试结果。

### Notes
- 只新增上述规划并追加本记录；源码、运行政策、数据和冻结报告均未修改。原用户工作区保留不动，文档位于隔离工作区的 `codex/autonomous-research-next-plan` 分支。
- 回滚只需移除新增规划并撤销本任务追加的记录；若以后单独提交，可对该文档提交执行 `git revert`。本轮未推送或合并，不沿用已完成代码任务的发布流程。

## 2026-09-26 - Task: 实现并验收有预算的真实自主研究闭环 U1—U4

### What was done
- 新增 bounded_research_v1、bounded_candidate_v1、bounded_model_v1、research_diagnostics_v1：连接既有编排入口、公共策略账户、Trial、预算及定性失败视图；固定数据/成本/时间范围，允许 AI 选择已有指标与门槛，保留父版本和修订理由，不授予正式资格。
- 修改公共后端路由、现有治理的研究范围授权和确认恢复、Codex 可选无工具设计模式；固定策略脚本仅增加候选规则/信号身份，旧默认行为保留。新增 CLI create/run/tick/status/revoke，补齐运行中撤销、账户失败停机、持久失败状态和中断恢复。
- 完成真实研究：5 次真实模型调用，5 个不同规则，4 次有反馈身份的修订；固定参照加5个候选均完成118天真实账户核对，差异为0。预算6次全部记账后自动停止，资格仍为NOT_ASSESSED，真实观察天数0。
- 新增测试与双平台CI清单、业务说明 docs/BOUNDED_AUTONOMOUS_RESEARCH_V1.md、真实摘要及87份原始JSON的证据索引 reports/bounded_research_20260926/；近期计划状态改为completed，后续正式筛选/Paper/组合不在该U1—U4实施包内。

### Testing
- 发布相关组合632 passed、23 skipped（真实采样未启用、符号链接能力及既有gbbq限制），JUnit位于隔离工作区外 bounded-ci-20260926-1.xml；闭环专项24 passed，跨进程强制退出/恢复专项1 passed。总计该发布验证657 passed、23 skipped。
- 额外编排/控制面回归最初66 passed、2 failed（含12项与发布组合重叠）：硬链接测试换到C盘支持目录后1 passed；剩余一项在未改动测试的fixture读取阶段StopIteration，仓库没有跟踪所需历史durable_frozen_candidate_contracts.json，未削弱断言或伪造样本。若干pytest退出临时目录清理出现既有WinError5，测试退出码仍0。
- 真实执行依次create、tick参照、tick第一候选、run余下候选；结束后新进程run核对87份持久JSON哈希与预算不变。证据导出首轮因比较预算视图生成时间戳失败，改为忽略非持久updated_at展示字段后复核通过，未重跑模型或账户。git diff --check通过。

### Notes
- 原用户工作区未修改；实现位于codex/autonomous-research-next-plan分支。真实任务保存在E:/llmwiki/autonomous-strategy-research-v1/bounded-research-20260926，不清除旧预算。模型未提供具体服务端型号/版本，证据保留NONE/UNKNOWN。
- 仅证明两股票、已曝光历史样本上的探索工程闭环；历史发布时间仍为建模假设，盈利不能作为独立确认或策略合格证据。没有创建自动采集任务、启动真实Paper或券商订单。
- 回滚可对本次实现提交执行git revert；保留运行目录、预算和报告供追溯，禁止删除后以新身份免费重跑。远端CI与发布结果在后续记录中单独注明。

## 2026-09-26 - Task: 合并自主研究闭环并推进评审、前瞻Paper与组合

### What was done
- 用户明确要求直接合并后继续开发。核对PR #21的35项检查均SUCCESS，以精确head 99c4d18合并；main合并提交4039d85。复用干净隔离工作区，从origin/main创建codex/qualification-paper-portfolio。
- 新增实施计划docs/plans/2026-09-26-002-feat-strategy-review-forward-paper-portfolio.md，明确档案准入、真实到达快照、前瞻账户、共享资金组合与验证边界。

### Testing
- 本记录时只完成合并前CI复核和现有源码接口调查；新阶段尚未验收，不声称测试或前瞻观察已完成。TQ技能前置检查确认Windows与通达信安装存在，TdxW进程未运行，未绕过前置条件调用HTTP。

### Notes
- 原用户工作区不动。回滚新阶段改动保留旧PR21成果；若撤销已合并PR21，应单独评估对main执行git revert -m 1 4039d85的影响。真实观察不能用合成日期或历史回放补足；后续完成结果另行追加。

## 2026-09-26 - Task: 接通策略评审、前瞻模拟观察与共享资金组合

### What was done
- 新增strategy_qualification_v1：冻结并核对完成研究的规则、预算、试验、诊断与结算；正式资格缺失时拒绝晋级，支持不可逆撤销。已有5个真实候选均核验通过工程观察准入，正式观察全部拒绝，结果归档reports/strategy_forward_paper_20260926/REAL_STRATEGY_REVIEWS.json。
- 新增forward_snapshot_v1及forward_paper_v1/forward_paper_engine_v1：通达信无缓存实际到达快照、CLOSE生成次日计划、OPEN使用收到的报价模拟成交，复用既有账户引擎。真实时钟不可注入，合成输入不累计真实天数，观察期公司行动停机。
- 新增portfolio_execution_v1：多策略同一账本，保留持仓归属和原策略目标权重，约束成员资金、单证券累计敞口、持仓数、待成交量、现金与费用，不预支卖出款。修复审查发现的目标权重丢失、Broker二次缩量、恢复期间撤销、权威查询异常与撤销提交中断问题。
- 提供scripts/run_strategy_lifecycle_v1.py统一档案/评审/采集/观察/日报/撤销入口、6个对应测试文件和双平台CI覆盖。docs/STRATEGY_FORWARD_PAPER_V1.md提供业务架构图、配置与操作边界，本轮实施计划状态completed。

### Testing
- 最终不重叠本地集合721 passed、21 skipped：regression.xml为592/21，lifecycle.xml为95/0，supplemental.xml为34/0，均在reports/strategy_forward_paper_20260926/。Paper18项含真实子进程强制退出前后与连续运行完整状态对比、重复快照、动态撤销、权威读取损坏和恢复超时。
- 5个真实研究候选档案/评审及真实CLI读取成功；未新增历史搜索试验或消耗模型预算。git diff --check通过。跳过项如实记录真实采样默认未启用与Windows符号链接限制；个别pytest退出仍有既有临时目录清理PermissionError，退出码0。

### Notes
- 工程功能完成不等于真实观察完成或策略有效：真实观察0天、正式合格策略0个；本机TdxW.exe未运行，未绕过技能前置条件调用HTTP。当前主板范围、预热来源待人工核对、观察期公司行动停机及独立统计确认缺口见业务说明。
- 原用户工作区不动；实现分支codex/qualification-paper-portfolio。回滚可对本轮功能提交执行git revert，保留E:/llmwiki/autonomous-strategy-research-v1/下研究与新档案，以及快照、账户证据。CI及合并结果另以实际远端状态为准。

## 2026-09-26 - Task: 修复生命周期CLI配置读取的CI路径告警

### What was done
- PR #22首轮SonarCloud指出run_strategy_lifecycle_v1.py配置读取缺少路径边界。增加load_config，将配置限定为观察目录父目录下的JSON，拒绝越界、路径重定向、非文件及超过20 MiB输入；同步操作文档，不修改或关闭CI规则。

### Testing
- CLI6 passed，新增3项越界/父路径/非JSON拒绝验证；证据reports/strategy_forward_paper_20260926/cli-path-fix.xml。与此前集合去重为724 passed、21 skipped；账户与研究实现未再变化，远端将重跑规定检查。

### Notes
- 初始实现提交07c35c2及PR #22已推送，修复另作提交。回滚本轮入口修复可git revert对应提交；整体功能回滚保留档案与观察证据，不删除历史。该修复不增加真实Paper观察天数。

## 2026-09-26 - Task: 实施正式有效性评审并保留统计校准失败结论

### What was done
- 从已合并PR #22的main 5033aa2，在隔离分支codex/formal-strategy-assessment实现；原用户工作区及其未提交研究内容不动。
- formal_statistics_v1与calibrate_formal_statistics_v1提供固定504日、完整家族Holm、独立重复校准。正式运行前冻结方法、2048重复、9支持过程与4域外过程；完整26,624条记录跑完，81个支持门中44个失败，method_approved=False。完整报告位于E:/llmwiki/autonomous-strategy-research-v1/formal-method-calibration-20260926，仓库保存摘要和数值对照；未换种子、删场景或降低阈值。
- formal_evidence_v1核验冻结后的真实OPEN/CLOSE原响应、实收时间、完整交易日历及价格参考连续性；正式账户backend复用原run_chain、公共策略入口和独立会计，实际分别执行正常费用、压力费用与持有基准。成交使用收到时的OPEN报价，收盘收到以后才决策。旧run_chain默认行为保留。
- formal_assessment_v1接通源研究权威绑定、终生3批次预算、先登记后暴露、全家族分母、可恢复的确定性内存账户、结算/统计/裁决报告和定性AI反馈。固定绑定实际校准哈希，拒绝调用方自编批准文件；真实校准未通过时不登记真实确认。保存结果独立复核账目并完整重放规则轨迹，防止删交易后重写收益；修复实际集成发现的零数量拒单兼容，仅允许原引擎SIZING_ZERO_AT_FILL证据。
- 统一CLI、策略档案与正式Paper准入已接入权威评审；正式Paper首版限定原评审的两只主板证券、100万资金与单策略配置，组合工程观察不等于组合有效。新增业务文档docs/FORMAL_STRATEGY_ASSESSMENT_V1.md、实施计划003及既有观察文档交叉说明。
- 5个真实候选完成准备评审，全部CONTINUE_RESEARCH，报告REAL_STRATEGY_REVIEW.json；没有将旧窗口标成独立、没有消耗新的确认预算。局部.gitattributes仅固定校准绑定5个源文件的原换行身份，避免跨平台签出失配，未全量转码。

### Testing
- 最终按price-only-indicator-validation-v1.yml完整30个测试路径本地执行：781 passed、25 skipped、0 failures/errors，625.83秒；JUnit与来源身份见reports/formal_assessment_20260926/regression.xml、VALIDATION.json。跳过项是未启用真实行情采样、Windows链接权限与禁止真实gbbq边界；一条既有Starlette弃用警告。
- 其中新正式评审23项含564 CLOSE+504 OPEN合成快照、504账户日、三个公共账户、独立核账及完整轨迹重放；账户13项、证据8项、统计15项均包含在上述总数。窄测method-evidence-cli.xml的31项与总集重叠，不重复累加。早期窄测存在既有pytest-current清理权限警告；最终全量使用独立临时目录，退出码0。
- 实际完整校准295.6秒；分段抽样与直接展开求和的最大数值差约2.8e-16。方法仍失败，工程测试通过不替代方法批准。
- 真实CLI的5候选评审输出与保存报告逐字段相同；完整26,624记录校准来源及固定哈希复核通过（结论仍False）。独立代码审查的轨迹绑定问题已修复并复核关闭；git diff --check通过。

### Notes
- 本轮完成评审工程与实际方法验收，但没有完成真实独立窗口验证，也没有策略取得正式有效资格。下一优先项是根据已记录的失败重新设计、预登记并校准统计方法，不能直接进入正式Paper。
- 当前真实证据范围要求先冻结再采集60预热+504账户交易日；停牌、公司行动或行动覆盖不明阻断。没有启动通达信、每日自动采集或券商交易。
- 回滚可对本轮功能提交执行git revert，保留校准失败、研究档案、曝光与预算目录；禁止删除记录后免费重跑。本地证据在上述报告目录，远端CI与合并状态以随后实际结果为准。

## 2026-09-26 - Task: 修复正式评审CI可靠性告警并完整验证数值等价

### What was done
- PR #23的SonarCloud唯一BUG门为cp_upper复合入参校验被识别为恒真、后续代码不可达。将校验按原短路顺序拆分，保持精确整数、范围、NaN及异常行为；未改变方法、种子、场景、门槛或公式。另将账户guard默认绑定本次receipt，去掉已由OSError覆盖的冗余异常类型。
- 原校准目录及仓库原摘要保持不变，新源码在独立formal-method-calibration-20260926-ci-replay目录预登记同一设计的等价重放。权威服务更新为实际重放报告哈希；新增CI_REPLAY_CALIBRATION_SUMMARY.json、REPLAY_EQUIVALENCE.json、REAL_STRATEGY_REVIEW_CI_REPLAY.json、CI_FIX_VALIDATION.json，不覆盖原失败证据。

### Testing
- 修复前后384组合法/非法输入的返回值与异常相同。完整重放298.05秒，26,624条记录逐条相同、summary完全相同，仍44/81支持门失败、method_approved=False。
- 修复相关67项测试全部通过（83.14秒、无跳过/失败），含504日完整采集/三账户/独立重放链路；与初始781项总集重叠，不累加。最终真实CLI与保存的5候选重放评审逐字段一致，候选结论与原评审相同。
- 原提交8857d42的已完成远端测试均通过，唯一失败为上述静态检查；修复提交需重新通过远端检查后合并，不关闭或降低CI规则。

### Notes
- 这是工程等价修复和确定性重算，不是重新挑选统计参数寻找通过结果；正式策略资格仍为0。旧校准源码可从8857d42恢复，旧外部报告与文件哈希均保留。
- 回滚本修复需整体git revert对应修复提交，使校准源码、固定哈希和使用路径一起恢复；不得只替换批准布尔值。原工作区未改动，PR #23继续使用隔离分支。

## 2026-09-26 - Task: 明确概率范围校验并保留第三次等价校准

### What was done
- 将Sonar仍误判的概率链式范围比较改为明确的两个边界判断，保留全部非法输入约束。固定方法、门槛、种子及公式不变；服务绑定最终真实校准报告。
- 新增CI_RANGE_REPLAY_CALIBRATION_SUMMARY.json、RANGE_REPLAY_EQUIVALENCE.json及REAL_STRATEGY_REVIEW_CI_RANGE_REPLAY.json，业务文档更新最终报告路径；此前两份完整校准与摘要保留。

### Testing
- 与初始实现384组输入对照完全一致；统计模块15项通过。独立目录完整重放292.31秒，26,624条记录与此前两次逐条相同，仍44/81支持门失败。重放不是新增独立样本。
- 最终真实CLI输出与保存的5候选评审逐字段一致，正式合格策略仍为0。此前完整781通过/25跳过和相关67通过不重复累加；最终提交继续接受全部远端CI检查。

### Notes
- 原用户工作区不动。回滚需整体revert本次提交，同时恢复统计源码与校准固定哈希；保留所有失败证据。静态检查修复不等于统计方法通过，也不替代未来独立数据验证。

## 2026-09-26 - Task: 修正正式统计评审方法并完成独立固定校准

### What was done
- 从已合并PR #23的main f51522f，在复用隔离工作区的codex/formal-statistics-v2开发；原用户目录未动。V1源码及完整校准固定身份复核未变，44项失败记录全部保留。
- 已知协方差计算定位旧重抽样有限样本方差低估；新增formal_statistics_v2.py和calibrate_formal_statistics_v2.py，固定504日8段均值t7单侧Holm，不重置分段账户，终生三批预算不变。完整方法和8192重复/18场景/126误判门/2功效门在运行前预登记，新seed2026092602，只执行此一套V2设计，未改阈值挑结果。
- 实际147,456条记录完整生成并按固定种子独立逐条重放通过；126项误判门和2项功效门全部合格。支持范围最接近预算项为MA10第三slot全零假设，上界0.9255%低于1%；预指定真备择SR2/SR4识别率23.68%/88.82%，下界22.76%/88.12%。这是预先指定合成范围的校准，不是通用市场保证。
- 服务按method_hash分派、保留V1解释、跨版本共用原权威预算。绑定实际V2完整报告9509c920d5adcd26431e7717d8cf615c74894b9ea067c5e9071b3d2893abebc4；受信pin路径避免每次重生成全部模拟，来源/完整报告身份仍校验。V2真实适用性未证明时明确阻断Paper，不接受外部布尔批准。
- 新增业务文档docs/FORMAL_STATISTICS_V2.md、规划004、reports/formal_statistics_v2_20260926证据；更新旧评审说明、CI及局部换行属性。约461MB完整证据在E:/llmwiki/autonomous-strategy-research-v1/formal-method-calibration-v2-20260926，仓库保留完整文件SHA、预登记和摘要。

### Testing
- 项目CI指定31个路径完整回归：811 passed、23 skipped、0 failures/errors，733.49秒。包含V1/V2各自504日真实公共账户实现的合成采集→三账户→重放集成；原有真实采样/Windows链接等边界跳过，一条既有Starlette警告。JUnit保存在regression.xml。
- 首次窄测72通过/2失败，定位为参考分位数精度和费用浮点括号不同；保持精度断言修正后49项通过，记录保留。绑定实际pin及优化分派后服务/CLI36项通过；这些是全回归的重叠集，不重复累加。
- 独立96点积分与t7公式最大绝对差5.55e-16。统计校准完整数据/种子/组统计/p值重放验证完成，退出码0；生成与汇总计时346秒不包含持久化和完整重放。源码冻结后未修改方法文件。
- 实际统一CLI以V2报告评审原5个真实候选：method_approved=True、全部CONTINUE_RESEARCH、strategy_qualified=False、确认预算消耗0。REAL_STRATEGY_REVIEW.json与实际输出一致。独立审查P2重复全重放成本已修复并复核关闭，无剩余已确认缺陷；远端CI与合并以随后实际结果为准。

### Notes
- 方法在固定场景合格不等于真实收益过程适用，不等于策略有效。仍需真实账户共同均值/分段近似独立的适用性审查和独立窗口；本轮未启动行情采集、Paper或券商交易。误判与功效各自95%同时置信，不合称95%联合保证；联合保守下界90%。
- 回滚可整体git revert本轮提交，使服务退回V1；不得删除校准、确认、权威或预算历史后重跑。新源码哈希归一化换行，完整数值重放只承诺所记录运行环境。

## 2026-09-26 - Task: 修复V2格式检查并保持校准来源可核对

### What was done
- 修复CI发现的校准脚本末尾空行和初次失败JUnit文本尾空格；脚本语法树不变，原始失败XML保存在外部研究目录formal-v2-initial-tests-original.xml。没有修改统计方法、参数、种子、阈值或测试断言。
- 在独立format-replay目录重生成全部记录并绑定最终报告427c509ffa60f2ad7ca122a5fc519b7aa602eb15826d9421e159fb5ee1074e22；新增FORMAT_EQUIVALENCE、FORMAT_REPLAY_PREREGISTRATION、FORMAT_REPLAY_CALIBRATION_SUMMARY和REAL_STRATEGY_REVIEW_FORMAT_REPLAY证据，业务文档明确最终使用路径与域外AR(0.9)失败边界。

### Testing
- 完整等价重放621.95秒、退出码0：147,456条记录及数学/CP摘要与初次V2完全一致，旧目录全部文件SHA未变，11个来源在重放前后未变，新增独立样本0。126项误判门及2项功效门仍全部通过。
- 最终实际formal-review退出码0，与初次真实5候选输出除校准身份外逐字段相同：method_approved=True，正式合格0，确认预算消耗0。此前811通过/23跳过及窄测不重复累加。
- 首次PR #24的Sonar及相关业务测试通过，部分CI仅因两处空白格式失败；本次修复接受最终提交的完整远端检查，合并以其真实结果为准。

### Notes
- 等价重放不是新独立校准，不扩大有效范围；AR(0.9)域外压力仍不满足预算。没有开始策略正式确认、Paper或券商交易。原用户工作区保持不变。
- 回滚须整体revert本次修复提交，使脚本来源、服务绑定及文档路径共同恢复；不得单独替换批准标记或删除原报告。

## 2026-09-26 - Task: 核验真实账户收益适用性和独立确认数据

### What was done
- 从main fe4816e创建codex/real-process-evidence-audit，复用隔离工作区；原用户工作区未改。核对5个真实行情驱动的历史模拟账户档案、源数据暴露声明、REFERENCE规则及正式权威目录。
- 新增docs/REAL_PROCESS_APPLICABILITY_20260926.md和reports/real_process_audit_20260926/RESULT.json、reproduce.py。后者是本次固定档案的复现工具，保存逐日绝对收益与来源身份，不是资格服务，不生成p值。
- 确认所有账户118日、2024-02-01至2024-07-31、已探索暴露、2026-09-26冻结；REFERENCE是51指标规则而非等权持有基准。实际权威目录不存在；研究根目录103份其他确认计划均为SYNTHETIC测试。未声称全E盘所有数据均已暴露或不存在其他历史保留集。

### Testing
- 通过现有Archive.load校验全部5个档案身份、来源/试验/结算链；账户诊断全部COMPLETE，账本投影逐字段相等。从净值重算118项日收益，复合收益与原净收益最大绝对差4.44e-16。没有在本次重新执行交易引擎。
- 复现脚本实际执行两次，退出码均0、JSON逐字段一致；每个账户仅1个完整63日分组、缺386个账户日，不满足504日固定设计。正式p值未计算，独立评定未执行，预算新增消耗0。
- 只新增审计文档、证据和本次复现脚本，不修改统计来源或生产代码；不重复运行无关完整CI。

### Notes
- 结论是证据不足，非策略有效或策略无效。不得重复/补零118日、用原始账户收益代替超额收益，或把探索窗口改标为独立。未注册确认批次、启动采集/Paper或交易。
- 后续需已暴露长窗口的公共账户/配对基准适用性研发、事先固定过程验收，再准备独立证据；现行前瞻协议需60+504日，无法当日完成。回滚本次交付可revert其提交；不涉及历史数据或权威预算修改。

## 2026-09-27 - Task: 跑通504日真实行情候选与费用压力及基准账户

### What was done
- 延续隔离分支codex/real-process-evidence-audit和预登记计划005；原用户目录未改。复用原5候选，不换股或调参；固定截至2024-07-31最后564日，60日预热，2022-07-06至2024-07-31共504账户日。
- BaoStock补齐两股各624日未复权日线/turn/状态、实际日历与6次现金分红/复权事件。984条旧日线和400条turn交集逐字段相同，分红前收差最大0.005元。原始数据、请求、抓取时间及hash保存在外部real-process-504-v1/data，不冒充历史实时可见证据。
- 新historical_process_v1.py复用公共入口和原交易核账引擎，强制HISTORICAL_MODELED并拒绝混入observed快照；formal_account_backend仅在有实测会话时添加decision_at，不放宽正式快照校验。新runner固定11用途，复用StrategyBatchGovernanceV1记录当前获准过程研究，保留旧探索和正式预算。
- 11账户全部完成并各完整重放一次相同，累计5544个账户日核对。普通净收益依次-3.19%、-14.69%、-2.43%、-38.28%、+13.52%，持有基准-0.50%；候选5压力后+1.84%、回撤25.04%、后半段超额不足。探索性V2 Holm校正p均1；不赋予正式资格或声称方法适用于所有真实过程。
- 新业务文档HISTORICAL_PROCESS_RESEARCH_504.md、实际摘要/范围/核验/预算/日志证据和两组测试；CI纳入新测试。原统计校准11个来源逐一核对未变，未重校准或修改批准hash。冻结实际账户代码原字节保存外部SOURCE_SNAPSHOT.zip。

### Testing
- 历史适配+原formal账户22 passed（26.37秒）；数据runner 8 passed（2.63秒）；正式评审/证据/CLI/档案/治理及runner相关回归72 passed（135.66秒）。8项包含于72，不重复累加；无失败或跳过。
- 真实11账户各504日、6次分红，原独立现金/持仓核账无issues；两次完整运行结果hash相等，BASE/STRESS决策逐字段相同，结算hash全部匹配。实际CLI再读已结算结果退出0，研究预算文件hash前后相同，正式预算消耗0。
- 第一次真实启动因输出目录未创建在预算/账户写入前报INVALID_RESEARCH_ROOT；修复为验证父目录后创建目标再验证，原错误日志保留，随后完整运行退出0。
- 本地审查覆盖来源/模式/身份/日期/公司行动/准入和治理路径，无剩余已确认缺陷。独立审查代理因服务使用额度未返回，不能声称通过独立代理审查。远端CI以随后实际记录为准。

### Notes
- 本轮证明工程长窗口链路和重复核账，不证明独立性或市场一般统计适用性。不把低样本自相关当独立性证明，不因无独立样本放宽准入；没有Paper或券商交易。
- 费用压力按原模型重新运行，费用/滑点变化可改变成交数量，决策规则不变。固定费率不是逐日历史实际收费。历史状态可见性及公告日期仍为供应商/建模口径。
- 回滚可整体revert本轮提交；保留外部数据、原始错误、START/SETTLEMENT和预算，不覆盖SCOPE或退还已消耗用途。runner拒绝未结算/失败的已消费用途，不能视为通用恢复服务。

## 2026-09-27 - Task: 接通诊断驱动的新候选、成本分段初筛及独立评审交接

### What was done
- 在隔离目录从main c6c99ba建立codex/diagnosis-driven-research，原用户目录及未提交文件保持原状。使用ec、ce-work、本地ce-code-review与ce-commit-push-pr流程。
- 新research_screening_v1.py从已结算账户重算成本/分段诊断与固定初筛，核对逐日收益、费用、规则、回执及已消费预算。新diagnosis_research_v1.py复用原BoundedResearchSessionV1、Codex模型调用、公共账户、策略归档及StrategyBatchGovernanceV1，不另造交易引擎。
- 生成前冻结政策、输入、源码和最多5次尝试。前轮及本轮已完成初筛的定性反馈进入下一候选；重放旧规则、未结算账户、篡改决策和绕过初筛均阻断，保留预算。
- FormalAssessmentServiceV1扩展可核验初筛交接，完整家族保留，只执行通过者的独立账户；未晋级成员继续占统计家族位置。真实数据/未来窗口/统计适用性/Paper门不放宽，没有通过者不占用正式批次。
- 新CLI、业务流程图和说明docs/DIAGNOSIS_DRIVEN_RESEARCH.md、计划2026-09-27-001、新增三组测试及CI接入。报告保存在reports/diagnosis_research_20260927，完整运行在E:/llmwiki/autonomous-strategy-research-v1/diagnosis-research-20260927。
- 实际5次AI调用均成功，5个新候选普通成本净收益依次-14.34%、-19.66%、-4.32%、-13.51%、-24.52%；压力成本和分段条件均未通过。结论NO_CANDIDATE_PASSED，无独立批次、正式额度消耗0、合格策略0。

### Testing
- 相关回归117 passed（418.07秒）；最终针对性复测23 passed（50.52秒，含重叠项，不相加）。覆盖真实公共账户/归档/预算集成、定性边界、分段和成本门槛、中断与运行区分、只执行通过者、完整家族与原正式服务兼容。
- 本次真实研究5个短账户候选及固定参照完成，11个长账户各504日独立核账无issues，合计5544账户日；共享基准逐日收益与上轮完全一致。
- 模型回执来源全部REAL_CODEX_SUBPROCESS、synthetic=false，原工具禁用及上下文校验通过；各轮反馈包数5/7/9/11/13。再次运行CLI退出0，结果和所有研究预算hash不变，没有重调模型或账户。
- 原V2固定校准与S1输入核验通过；未修改校准方法或来源白名单文件。源码原字节已保存SOURCE_SNAPSHOT.zip。审查为本地审查，不声明独立代理审查；远端CI以实际PR检查为准。

### Notes
- 实现了研究接线，不证明新策略有效；真实独立确认未发生。正式交接的通过分支由合成测试验证，真实本批均被初筛淘汰。当前候选仍限定为指标上升投票规则，没有增加持有期等新策略表达能力。
- 独立数据仍要求冻结后采集的60日预热与504日账户；没有用旧历史补造独立证据，没有启动长期定时任务、Paper或券商交易。
- 回滚点为main c6c99ba；停止新CLI后可revert本次功能提交。保留外部模型/账户/预算证据；不得删除旧记录、退回已消费额度或修改冻结范围续跑。

## 2026-09-27 - Task: 制定自主研究系统剩余七项贯通计划

### What was done
- 基于已核对的main 386696b，在既有干净隔离工作区创建codex/seven-gap-completion-plan；用户原目录和未提交修改保持原样。
- 使用ce-plan编写docs/plans/2026-09-27-002-feat-autonomous-system-completion-plan.md，覆盖策略表达、数据范围、统计评审、独立验证、Paper、组合和统一入口七项要求，分为五阶段、十二交付单元。
- 计划包含业务架构图、每单元的依赖/文件/测试/验收、协议兼容和回滚、真实观察等待处理；明确工程交付与全计划实证完成不同，不承诺必然产生合格策略。
- ce-doc-review采用本地一致性与可行性审查；明确早期采集不自动取得独立资格，以及扩展策略/股票池后必须重新核验方法支持范围。没有启动实现、研究模型、数据采集、定时任务或Paper。

### Testing
- 文档结构及路径核对：七项要求、U1至U12稳定编号、五阶段和八个业务验收例子；既有源码引用按实际路径核对，拟新增文件明确标注。
- 文件引用规范化检查首次发现api.ts有两个同名落点，在写入前中止；依据计划的控制台范围显式选择frontend/src/console/api.ts后重新执行成功，共展开31处简称引用。
- 最终文档检查通过：7项要求、5阶段、12单元、8个验收例子、42处已有文件引用有效、29处拟新增路径已标注，无意外缺失路径；git diff --check通过。本次不运行应用测试、统计校准或完整CI，不声称独立子代理审查或新增真实策略验证。

### Notes
- 本计划状态active，用户要求后续才执行；当前首要阶段为数据资格清单与可执行的独立验证协议。真实数据、方法适用性或合格策略不足时保留待实证状态，继续可独立完成的工程工作。
- 回滚点为main 386696b。本次仅新增计划并在progress.md追加本段；回滚时仅撤销这两处文档改动，保留原历史记录及所有研究证据。当前未提交、推送或合并本次文档。


## 2026-09-27 - Task: 执行自主研究剩余七项计划（集成开发）
### What was done
- 在独立工作树 codex/seven-gap-completion-plan 开发，未修改用户原始脏工作区。
- 新增数据资格投影、验证协议、统计适用性诊断、状态型规则及多股票账户、动态分红证据、组合观察评审、有限任务调度和统一入口；正在完成正式评审及整体验收。
- 真实目录元数据引用 28 条既有账户授权/执行记录，已知历史窗口标记已暴露；真实双股原始响应重新校验通过（1128 日线、1128 换手率、1008 状态、6 个分红事件）。
- 已对原 11 个真实账户完成独立数值复算，计算一致，但真实过程统计适用性继续记为证据不足，不授予资格。
### Testing
- 状态账户历史/观察模式 6 项测试通过；诊断缺日/权益差额反例 2 项通过；U1/U5 初轮 49 项通过；U4 修正完整性测试夹具后 26 项通过。
- 生命周期服务稳定源码窗口 8 项通过，调度/规则等相关 57 项通过；前端 11 项测试与构建通过；启动/loader 3 项通过。
- Paper/组合首轮 41 项通过、1 项失败、1 项夹具错误：组合测试 RSI 饱和导致成员无交易，已修正合成行情而未放宽阈值；夹具错误为并行源码改动触发 BOUNDED_SOURCE_CHANGED。统一稳定回归待完成。
- 测试收集成功：3107 项，1 项既有范围外集成模块跳过。以上不是整份计划完成声明。
### Notes
- 文件用途及业务边界见新增 docs/*V2.md 和对应模块文档；原统计方法来源及预算账本保留。
- 真实未来观察、独立统计支持和真实合格策略尚未取得，不以合成数据替代。
- 回滚点：386696b10e601cab7c228dc8a454e89af60aefcc。代码可回滚；已有研究调用、预算和证据只保留，不删除以重试。

## 2026-09-27 - Task: 七项计划真实验证与最终验收修复
### What was done
- 原 main 仍为 386696b；在隔离分支继续完成 V2 诊断/初筛/正式评审、三股真实历史账户及统一入口。
- 固定新增 000002.SZ，保存 BaoStock 原始响应；三股 504 日、8 个分红事件独立核账通过，重复完整结果一致。真实结果为亏损，不授予策略资格。
- 启动预登记两次模型额度的真实 V2 研究；第一次调用被模型服务404拒绝，保留失败回执及已消费额度，没有自动重试或更换目录重置预算。
- 新增 docs/AUTONOMOUS_SYSTEM_ACCEPTANCE.md；更新业务使用说明；原始失败回归压缩保存在 reports/autonomous_completion_p0_20260927/diagnostic-regressions.zip。
- 修复浏览器创建预览将预算读取时间当成业务变更的问题；只排除预算展示时间，余额、保留额、绑定和任务变化仍受保护。
- 将合成504日夹具放入 research_factory/conftest.py 共享，且在完成构建后立即撤销补丁，避免污染真实loader测试和重复跑完整夹具。
### Testing
- 稳定源码窗口长回归224通过、4失败，6457.87秒；4失败均由上述会话级补丁泄漏导致，断言未弱化。新的整组结果由最终CI补证。
- 公共回测与持久Paper一致性1通过，154.84秒；首交易日/loader定向10通过；运行器恢复/篡改9通过；最终预览及运行器18通过。
- 前端12项通过并构建；新增Paper预登记风控及评审点正在定向验证。
### Notes
- 真实账户399.16秒完成两次运行；实时行情服务未就绪，真实观察日0、真实新候选0、真实新增资格0。具体边界与等待入口见验收文档。
- 当前未发布；待补充风险控制验证及CI后提交合并。回滚基线386696b；保留原始数据、源码归档、预算和失败调用，不删除以重试。

## 2026-09-27 - Task: 七项工程交付发布准备
### What was done
- 功能提交4529658：版本化规则、真实共享账户、固定初筛及正式交接、公司行动、组合观察、有限任务和统一页面/CLI。
- 完成Paper可选预登记观察政策：回撤/评审点停止新买入，保留原交易限制下的退出；评审点后至少一个退出交易日。旧无政策会话保持兼容。
- 浏览器最终任务browser_acceptance_final_20260927完成创建/启动/暂停/恢复/执行，1/1次；旧任务源码变更拒绝事实保留，没有改写身份。截图及矩阵见验收目录。
- ce-code-review只读复核预算预览、运行器恢复及原资格/预算边界，已修复确认时间、结算恢复、首交易时点和测试夹具泄漏；当前无已知高置信未修复代码缺陷。
### Testing
- Paper观察政策与CLI最终17项通过（45.25秒）；前端最终12项及构建通过，原大包提示保留。
- 新增Linux/Windows受影响模块工作流；冻结版本的最终CI/PR状态以远端检查与交付回复为准，不提前记为通过。
### Notes
- 工程交付不等于全计划实证完成。真实AI模型服务受阻；独立数据、统计适用性、真实Paper和组合观察仍等待，不降低标准。
- 交付包含业务文档、架构图、四列矩阵、原始失败诊断及等待入口。可通过反向提交回滚4529658；保留全部暴露、模型调用、预算、账户和外部冻结源码。

## 2026-09-27 - Task: 修复 PR 27 的 CI 检查问题
### What was done
- 删除正式规则测试文件末尾多余空行；创建任务按钮明确使用 submit。
- 正式账户范围逐项核对资金、持仓上限、单股上限和股票范围，保留两股限制，增加逐字段拒绝测试。
### Testing
- 范围及原登记边界定向测试 6 项通过；前端 12 项通过，生产构建通过（原大包提示保留）。
- git diff --check origin/main 通过。上一轮最终跨入口、Paper 风控和生命周期测试 17 项通过（224.36 秒）。远端新提交 CI 待运行。
### Notes
- 修复范围限于 CI 发现项；不调整统计门槛、授权或真实证据状态。可反向提交本次修复回滚，保留历史账户的冻结源码。
## 2026-09-29 - Task: 执行可信策略研究计划（实施中）

### What was done
- 在 codex/trusted-strategy-research 隔离分支实施 U1–U6 的首批工作；保留主目录其他 AI 的研究原件。
- 新增 V3 显式指标参数与退出声明、数据目录与公共提交服务首版；账户退出、能力查询、报告字段修复与生命周期宿主接线仍在集成。
- 当前计划保持 active，未将首版代码视为完整计划完成。

### Testing
- V2/V3 规则与退出基础测试：80 passed。
- 数据/能力/报告/账户退出首批集成复查：25 passed、1 failed 后停止；失败为停牌样本先被历史数据资格拒绝，正在修正测试分层及后续问题。
- 首次完整运行还遇到其他 pytest 临时目录权限错误；后续验证使用隔离分支 .venv 下独立临时目录。

### Notes
- 尚无本次真实固定账户验收、独立方法资格或真实 Paper 天数结论。
- 回滚点为 main 的 2931cbde08cef9c81bbd9807a79eef12ad1259ab；当前所有实现只在隔离分支，弃用该分支即可，不应删除主目录研究文件。

## 2026-09-29 - Task: 推进可信研究计划 U9–U15 与发布前审查

### What was done
- 扩展连续研究的跨批统一预算，DATA/VERIFY 使用固定受限子进程，账户继续复用公共治理；每次宿主 tick 最多推进一个候选。
- 接通公共 CLI、工作台逐对象权限、V3 策略档案与 Paper 路由、只读每日数量计划；能力目录和示例由同一服务生成并加入 CI。
- 增加完整家族的未来确认协议及五万元方法适用性接口。方法研究冻结六个 504 日账户，完整枚举共同状态反例所得家族误报率 0.5，结论 UNSUPPORTED，不授予真实资格。
- 独立审查发现退出决策未核对实际执行、合成治理权限混用两个 P1，已分别补独立退出订单/成交核验和真实提交权限隔离；另修复公共宿主读取冻结文件格式不兼容。
- 原件和新增模块均在 codex/trusted-strategy-research 隔离工作区；主目录其他 AI 的研究脚本、结果及未提交 progress 未改动。

### Testing
- 连续研究受限 worker 专项 9 passed；预算 18 passed；方法研究测试 6 passed；公共 CLI 10 passed；新档案 CLI 分派 4 passed。
- 修复后退出核验 22 passed；公共宿主与确认协议 17 passed；权限隔离和确认协议 9 passed。
- 前端权限 4 passed、Vue/Vite build 通过；能力文档/示例 6 passed。独立真实模型接线一次成功，费用 UNKNOWN，仅作为接线证据。
- Paper、每日计划和跨阶段集成待源码稳定后统一验证；并行源码变化触发的 SOURCE_CHANGED 拒绝保留，不计作通过。

### Notes
- A 池真实数据资格检查通过，已准备四份固定规则/资金验收授权，尚未启动账户；B 池完整原件缺失，BaoStock 登录失败，替代源检查未解决，不得标记 S1 完成。
- 默认真实模型无法证明 token/费用硬上限，自动研究创建明确拒绝；不将模型接线成功冒充费用保障或无人值守验收。
- 本记录为阶段进展，不代表计划、CI、推送或合并完成。回滚点为 2931cbde08cef9c81bbd9807a79eef12ad1259ab；停止新宿主并保留授权/预算/原件，只回退软件版本，不删除研究账本。

## 2026-09-29 - Task: 完成两池真实账户验收及发布前修复

### What was done
- 公共CLI执行原定两池三规则5万元及10万元对照，共21账户、每账户122交易日；全部通过原件绑定和逐日独立核账。三类退出均实际触发。
- 恢复演练证明账户结束后换进程完成核验/报告、再次启动不改变结果/结算/预算字节。21账户独立复核及现金加1元反例拒绝证据已保存。
- 修复整仓FIFO退出核验、真实Web权限、宿主停止后继续派发、Windows DATA查询Git进程配额；发布凭证独立于执行能力身份。
- 新增/更新docs交付追踪与操作说明；真实原件565项打包并逐字节校验，方法报告保留UNSUPPORTED，不授予策略资格。

### Testing
- 退出/发布凭证34项通过；动态换手率依赖6项通过；接手演练1项通过。
- 相关集中回归252 passed、1 failed；失败定位为Windows venv+Git进程配额，修复后相关50项全部通过。第一次长集中回归主动中止，未计作通过。
- 前端12项测试通过，最终构建成功。双平台完整回归待本次PR CI，绿色后才合并。
- 真实21账户共2562账户日核验通过，发布凭证PUBLISHED_METADATA_VERIFIED；仅工程证据。

### Notes
- 首次BaoStock登录失败原件保留，后续同范围9请求成功，未换池/规则/窗口。第一次进程演练仅因未配置PYTHONPATH在导入前退出，未启动账户；原失败记录保留，补环境后接续同一任务。
- 五万元正式方法不适用；独立验证、真实Paper/组合资格、真实模型费用硬上限仍按各自条件等待，不能把本次工程通过说成找到有效策略。
- 当前主目录研究与progress改动保留；尚未在本条记录中声称合并/推送完成。回滚点为2931cbde08cef9c81bbd9807a79eef12ad1259ab；发布后用revert回滚本功能提交，保留账户原件。

## 2026-09-30 - Task: 发布可信研究计划并修正 CI 上下文

### What was done
- 已推送 82a1133 并创建 PR #28；修正 autonomous-completion.yml，将 runner.temp 的引用移至测试步骤环境，避免 GitHub 在作业级环境解析时拒绝工作流。

### Testing
- 首次远端运行在创建任何 job 前失败，不计作测试失败或通过；修正后由远端重新验证。

### Notes
- 冻结源码与真实验收原件未变；回滚本次修正可还原工作流该项环境位置，但会恢复解析问题。主目录研究文件仍保留。

- CI 另发现冻结原件的 CRLF 被当作空白错误；通过 .gitattributes 明确原件换行格式，两个已绑定源码文件和失败日志保留已有原字节。未修改测试断言或验收哈希，发布凭证仍须核对精确源码身份。

- Windows CI 文档示例的 TemporaryDirectory 使用重定向临时目录，触发公共入口路径保护。仅将生成器自身创建的临时目录解析为规范路径；公共入口拒绝外部重定向路径的安全检查保持不变。新增别名路径回归。

- SonarCloud 报告失败：逐项复核核心路径告警未发现当前本地单用户入口的越权链，auth 告警指向内容哈希，冻结方法拒绝分支为设计。保留外部失败状态并写入 docs/TRUSTED_RESEARCH_STATIC_REVIEW.md，不关闭扫描。表单按钮补显式 type=submit，前端 12 项测试通过。

- 完整 Linux 回归：481 通过、1 跳过、1 个旧测试替身签名失败。测试替身补 real_binding_ids 参数，同时新增缺省为空和显式登记列表传递断言，生产源码不变。
- JUnit 显示旧 formal_rule_adapter_v2 的全家族单例耗时 916.6 秒；CI 将该文件独立并行执行，原测试文件集合保持完全一致，不删除或跳过检查。

## 2026-10-01 - Task: 执行全范围选股与可信账户研究计划

### What was done
- 在 `codex/full-universe-research` 隔离工作区扩展公共 V3：沪深主板与创业板使用相同51指标、指标组合、固定成本止损/止盈/移动止损、统一五万元账户、独立核账和显式恢复。原BaoStock请求与旧V2行为保留。
- 新增范围/TDX来源适配、独立交易日历、历史状态与公司行动资格、板块制度、受限公共扫描和固定输入、共享账户、价格对照、检查点与独立证据组件；配套 `scripts/prepare_full_universe_*`、固定验收/规模探针、CLI/工作台/能力说明与三板块测试。
- 全E盘仅按路径元信息盘点218993个候选文件；授权窗口历史目标4607、对应缓存4463、缺行情144，缓存另73只历史身份等待。486日2239002条状态保留原次日开盘可见及MODELED声明，不补造同日可见性或不存在的公司行动覆盖。
- 实际大表触发内存失败后，按原字段/类型/逻辑身份改为分批Parquet读取、独立逐列复制和原pandas逐列哈希、分批冻结写入；未扩充2GB/900秒限制、未删目标、未重置旧174个候选或新工程账户预算。所有失败记录保留。
- 使用说明在 `docs/FULL_UNIVERSE_RESEARCH.md`，交付边界在 `docs/FULL_UNIVERSE_DELIVERY.md`；原实施计划保持active，真实U8未取得账户验收不能用软件测试关闭。

### Testing
- 新功能20文件本地集中检查359 passed，341.09秒；随后哈希峰值修复定向68 passed，包含旧身份逐字节/对象类型/缺值/Arrow/排序与原表独立性验证。两次结果分开，不相加冒充总测试数。
- 前端21项测试和构建通过；能力说明与6个公共预览示例检查MATCHED，Git空白检查通过。
- 4536只三板块合成证券，实际2GB/900秒受限，两种分批/相反遍历均逐日处理全部证券、账户核对通过、经济身份一致；该证据仅含5个账户日，不证明多年真实账户的资源能力或盈利。
- 真实全范围容量复核、Windows/Linux相关CI、合并与主目录同步尚在执行，本条不提前称通过。旧集中回归首次失败及后来因源代码修复主动中断的重跑不计通过，最终以稳定源码远端回归为依据。

### Notes
- 真实U8依赖：144只行情来源、当时可见的状态证据、完整现金/非现金公司行动、除权参考价及历史主清单完整性。模型单位与TDX授权窗口数值一致只属于交叉证据，不授予策略资格。
- 本机数据/部署/授权/失败与冻结原件在忽略的 `data/full_universe/` 和 `reports/full_universe_runtime/`，不上传公开仓库；旧绝对路径任务须保留原工作区。主目录其他AI的progress和未提交研究文件保留。
- 可回滚到157945d：停用FULL_UNIVERSE_DEPLOYMENT_V1恢复原部署，或对本次合并提交执行revert并检查；保留新原件和预算回执，不退回已消费额度。

- 测试与真实检查补记：稳定源码20文件集中回归374 passed，362.39秒。大表类型推断取得真实Windows原生异常栈后，临时数组改为逐列局部system pool，原类型/pandas元数据逐项一致；开发探针成功，不作为公共验收。
- 正式公共两份固定规则各完成4607目标准备/冻结/资格检查，原2GB/900秒限制实际生效，分别309/307秒、worker退出码0；输入身份同为a49395dd5959d7742cd6abb5246b891a6f11437207e466f4174e16b6c5178718。两者SCAN_DATA_GAPS，条件未算4607只保持未知，freeze以UNIVERSE_ACCOUNT_INPUT_NOT_READY:SCAN_DATA_GAPS拒绝，未创建账户预算。
- 新公开摘要为reports/full_universe_acceptance/ENGINEERING_SUMMARY.json；本机实际SOURCE、冻结及历次失败原件保留在忽略目录。板块制度文件固定LF以保持跨平台及主目录实现字节，原九源码和旧发布原件换行约束保留。远端CI与合并同步以下续交付回执为准。

## 2026-10-02 - Task: 修正全范围CI依赖兼容与完成发布前验收

### What was done
- 初次Linux/Windows全范围CI各有13项失败，定位为pandas3恢复object字符串及None时改变冻结身份；Provider和冻结恢复增加保真读取，输入查询保留原数值/日期标量语义，纯文本哈希避免恒等map与重复展开大表来源字符串。未重写旧冻结身份或削弱核验断言。
- 来源边界补强Windows根相对路径/盘符/UNC/父目录的探测前拒绝；归档先核对当前实际provider登记，worker先核对父进程持有的原intent_identity。合法外置元数据及可信本地插件路径保持已有授权边界。新增对应反例，静态扫描逐项结论追加到docs/TRUSTED_RESEARCH_STATIC_REVIEW.md。
- 更新docs/FULL_UNIVERSE_DELIVERY.md及reports/full_universe_acceptance/ENGINEERING_SUMMARY.json，区分旧依赖结果、最终锁定依赖结果和真实資料缺口；历次资源失败和慢速诊断探针均保留，不当作验收通过。

### Testing
- 最终稳定源码、CI锁定Python3.13.5/pandas3.0.5/PyArrow25.0.1：21份新功能测试文件440 passed、1 skipped，1491.71秒，0失败/错误。跳过仅因Windows E盘不支持真实符号链接，另有解析重定向拒绝测试；Linux真实链接验证由CI补证。
- 两份真实正式公共检查在原Windows Job/2048MiB/900秒限额内各处理全部4607目标，分别600.55/669.80秒、worker退出码0。输入身份同为2bd65c621455b406ddd02fafb6740f990f69920e42f38ab0c65b37866aa3d169；两者SCAN_DATA_GAPS，条件0/未知4607。两份freeze均按原资格边界拒绝，ENGINEERING_BUDGET.json仍不存在。
- Provider定向两套环境各56通过/1跳过，扫描各44通过，输入各101通过；语义、真实受限worker及伪造登记/握手反例均通过。本条不提前将尚待重跑的远端CI或合并同步记作完成。

### Notes
- 当前真实U8和整份计划仍active，144行情来源、历史状态可见性、完整公司行动、官方除权参考价和历史证券清单完整性未补造。策略资格、独立确认、真实Paper观察仍为否/0天。
- 本次仅追加本任务验证和发布修复；主目录其他AI研究/progress待同步时原样备份保留，旧绝对路径冻结任务继续保留原工作区。回滚可revert本次修复提交，或停用全范围部署退回157945d原入口，保留所有原件、失败和预算。

## 2026-10-02 - Task: 给完整R1回归足够的CI作业时间

### What was done
- fa01a5e的Windows/Linux新全范围CI各441项全部通过、0跳过，实际受限4536只合成演练的经济身份均为a9daf1d7db48ada94aa2f157e18d50ad163fb6988f1750dd9c48277e837cfb4c。
- 旧Windows R1作业及一次仅该作业的重试，均在20分钟工作流总时限后被GitHub取消；日志/JUnit分别327/287项通过，0断言失败，后续部分未执行，不记为完整通过。
- .github/workflows/r1-source-data-certification.yml仅将control-plane工作流总时限20改40分钟，给安装、前端和原有多层回归完整执行时间。formal-services原20分钟、测试清单/断言、账户2048MiB/900秒和研究预算均保持。交付文档追加此事实及实际本地/远端Python补丁版本。

### Testing
- 两次取消日志、首次不完整JUnit与定向重试回执已保存到本机忽略目录；前次同工作流Windows12.53分钟通过的记录用于决定仅做一次定向重试，未将该旧提交结果替代新提交。
- 本次CI配置差异为单一整数时限；YAML解析确认control-plane为40、formal-services仍20，git diff --check通过。后续新提交仍须远端完整检查通过。

### Notes
- 本改动只增加CI编排等待时间，不放宽回测或模型资源限制，不删除失败/取消记录，不更改源码或数据资格。回滚此配置提交会恢复旧20分钟作业上限；核心软件及双规则真实检查的源码字节未变。
- 整计划真实U8仍active；本机原件、主目录未提交研究及冻结绝对路径继续保留。

## 2026-10-02 - Task: 复用旧BaoStock原件并按逐股年度缺口补齐全范围资料

### What was done
- 新增BAOSTOCK_FULL_UNIVERSE_V1适配与正式登记准备器，旧BaoStock原响应保留来源/查询/原件哈希后进入公共全范围入口；TDX资料不包装成BaoStock。实际查询年份在读正文和哈希前核验，旧report年度登记范围按真实查询另建新版本，原件和旧身份不改。
- 固定C50K_V3_MF4_A_SEGA、原A池、20220706—20221230、50000元，在新旧公共入口各执行BASE/STRESS账户，共4作业。输入一致、每组122个账户交易日/8笔成交、逐日决策和账户经济结果差异为0。新路径新增的分红税审计字段逐项核对实际事件/到账/政策后只读复核通过；原严格MISMATCH记录保留，已完成作业未重跑，预算文件哈希不变。
- 从本地TDX原DAY补回144只缺失缓存目标，从已有全市场BaoStock raw3复用全部4607目标的状态/前收盘价；状态新版本显式使用MODELED_SAME_SESSION_VENDOR_STATE，原NEXT_SESSION_OPEN数据集原样保留，均不升级为历史可见时间已验证。
- scripts/prepare_universe_supplements_v1.py、prepare_universe_actions_v1.py、collect_universe_gaps_v1.py按股票/operate年份/ADJUST窗口列缺口并保存原响应、请求开始、完成见证和批次日志。缺少可选换手率不影响不依赖该字段的策略；依赖该字段的股票保留UNKNOWN。SCAN保留全部目标分母并允许已具备条件的股票计算，ACCOUNT仍要求完整资料。
- 首轮采集完成373批；并行追加采集登出干扰了主会话，原374批明确BLOCKED。逐项核验复用失败批中4个成功请求，主成功3361/实际尝试3362，追加16项已完成。新增prepare_universe_collection_continuation_v1.py只读生成15043项剩余清单（含失败1项的一次显式续采），继承实际次数，顺序续采中；未将未完成补采记为完成。

### Testing
- 冻结CI依赖环境全范围28份测试文件561 passed、1 skipped，1107.21秒；Windows符号链接能力限制导致一项跳过，Linux CI继续检验真实链接。
- 最后续采/collector/actions/公共SCAN定向100 passed，65.77秒；续采工具单独与collector20项通过。生成能力说明及3份公共示例检查MATCHED。新旧4账户治理、输入、实际账户结果和只读预算证明保存在本机reports/baostock_universe_bridge_v1/。
- 主目录原有pandas2依赖环境的旧数据适配/登记/账户对照/续采定向74项通过，8.65秒；不要求其他AI重建环境才能使用已验证接入。
- 审查确认并修复续采分叉重复风险：原queue/journal身份绑定原目录外唯一计划与采集root，当前合法续采仅新增登记、未重启网络会话。最终续采/collector23项通过，5.60秒；重复计划与不同collectorroot在登录前拒绝，同root恢复保持累计计数。
- 后续顺序采集在340个成功批次后遭远端WinError10054/10002007；该失败批次前6项已成功。有限父链续采核验祖先原件/队列/日志/唯一登记，真实累计6429次、成功6427项、剩余11977项，失败记录不改。每个操作最多3次显式重试（含原请求最多4次），分段采集用原有max-batches后同root恢复；27项续采/collector测试通过（6.94秒），包含旧receipt兼容、跨代累计及重试上限。
- 首次全4607真实阶段SCAN在900秒被原资源守卫终止，SCAN_BLOCKED/RESOURCE.timed_out=true保留，账户/预算均未创建。定位到每股每天扫描全市场公司行动proof的三重遍历，仅对已验证完整proof按证券建立区间索引；原来源校验、全分母、输入身份、False证明和全局阻断不变。104项输入测试通过（4.43秒），同固定规则/日期/全部目标在原限额内重新核验中。
- 索引修正后的输入/公共SCAN/真实资源边界151项通过（807.61秒）；4536只合成证券两种分批和遍历次序实际受限执行、五日逐日全分母、经济身份和核账一致。第二次真实SCAN未超时，但000790.SZ停牌除息触发CAUSAL_PRICE_EX_DATE_MISSING：年度/ADJUST/状态均已有，不是缺原件，不补造停牌成交bar或删除分红事件；该具体输入不支持应按证券保留UNKNOWN，不全局遮盖其余股票。
- 仅公共SCAN对精确CAUSAL_PRICE_EX_DATE_MISSING按股保留UNKNOWN/原reason/None条件计数，继续其他股票；scanner默认严格ACCOUNT行为和其他异常继续抛出，现金变换算法/原件/输入身份不改。scanner及完整公共SCAN55项通过（53.65秒），包含停牌除息混合范围与真实OS资源/冻结/权限边界；原失败仍保留。
- 4607旧raw3来源均核验成功、TDX/Bao价格冲突0；全范围阶段扫描和剩余补采执行结果继续保存为新记录，不替代原失败/旧验收。

### Notes
- 本次复用和采集不授予策略资格、独立验证或Paper资格，不重置174次历史候选及研究预算。现金分红可选送转字段仅沿用已验证旧解析中空字符串视为0的厂商口径，实际非现金事件、来源冲突、缺条款保持缺口。历史证券名单完整性、单位来源及非现金/退市处理的原证据边界仍保留，真实U8仍active。
- 新说明集中在docs/BAOSTOCK_UNIVERSE_BRIDGE.md及FULL_UNIVERSE_DELIVERY.md。原件、失败、冻结作业和预算均不上传公开仓库。回滚源码可revert本次提交或退回a64b946；停用新登记清单可继续使用原BaoStock入口。冻结作业绑定绝对路径，保留工作区原件；主目录同步仅合入本次文件并保留原未提交研究及progress插入记录。
## 2026-10-02 - Task: 发布前修复账户对照工具的文件读取边界

### What was done
- 远端SonarCloud指出run_baostock_universe_parity_v1.py先读取索引中的结果路径再核对目录。现以JOB父目录和安全作业名派生INDEX/RESULT/SETTLEMENT路径，在读正文前拒绝目录冲突、越界和符号链接重定向，原三方哈希和成本核验不变。

### Testing
- parity定向47项通过（2.81秒），包含恶意result/settlement/root/name及INDEX/RESULT/SETTLEMENT重定向的读取前拒绝；独立只读审查无剩余路径边界问题。
- 实际旧新账户只读复核PARITY_VERIFIED：BASE/STRESS各122个交易日、8笔成交，逐日决策及账户经济结果零差异；VERIFIED_PARITY_fdffdd5babbb35339720701ef95a9f4d1506ea7f654b92ea13f5eb516b223b4b.json保留原MISMATCH，预算哈希未变，未重跑账户或消耗新预算。

### Notes
- 此修复不修改交易规则、账户算法、数据原件或研究门槛。回滚仅revert本次路径修复提交；发布仍等待当前提交远端CI完整通过，不跳过质量门槛。


## 2026-10-02 - Task: 接通送转股份并核验分红及日期差异

### What was done
- 新增 `universe_corporate_accounting_v2.py`、`corporate_action_price_v2.py`；接通全范围股份账户、因果价格、成本和移动退出、恢复状态与独立算术核账。旧现金V1路径保持原语义。
- 更新 `forward_paper_engine_v1.py`、`universe_account_backend_v1.py`、`universe_account_inputs_v1.py`、`universe_rule_exit_v1.py`、`universe_evidence_v1.py`、`engine_replay_recovery_v1.py`、`universe_signal_scan_v1.py`、`universe_scan_service_v1.py`、`universe_submission_v1.py`；修复原股/新股同日订单重复使用成交量额度及新增股后的真实平仓冷却。
- 新增 `scripts/prepare_universe_actions_v2.py`、`scripts/resolve_universe_action_dates_v1.py`，按原件/派生文本/引文绑定声明物化股份及独立分红项目，日期收据只读重算；旧原件和旧冻结目录保留。
- 更新 `universe_benchmark_v1.py` 以保持理论股份+现金财富；更新 `research_capabilities_v1.py` 和生成的能力说明，说明股份条款、价格与账户覆盖分别判断。
- 维护 `docs/CORPORATE_ACTIONS_V2.md` 及三个全范围说明的版本入口；新增/更新对应测试，Windows/Linux full-universe CI加入四个新测试文件。
- 真实原件准备保留4,607只目标、7,074项事件：4,531只价格覆盖、4,026只账户公司行动条款覆盖；576条日期差异中546条有有限解释，30条未知。598项股份事件仍缺税源/到账证据，覆盖515只股票；48条现金数值未知保持缺口。
- 官方公告核实000028.SZ的股本溢价转增、到账/上市日期，以及601966.SH年度0.286与季度0.091合并分红。账户现金0.377、差异化除息参考0.37444分别保存。

### Testing
- 新公司行动、价格、准备与日期收据42项通过；能力/输入/旧发布包及基准133项通过；独立核账47项通过；股份冻结日期守卫6项通过。
- 完整全范围组运行926.89秒：657 passed、1 skipped（Windows无符号链接权限），1 failed为旧测试仍断言“送股一律不支持”。现已改为精确断言“股份条款未知仍阻断”，并包含在133项通过的后续回归中；没有降低拒绝要求。
- ce-code-review对抗审查原反例发现的重复容量已修复并复验，末审无P1/P2；连续二次转增、部分持仓与恢复/篡改反例已核验。
- 真实000028固定工程账户BASE/STRESS：600→780股、红利480元、卖出现金分红补税96元、转增税0。登记前和除权后两次中断恢复与连续结果一致；最终只读作业索引核验及全池扫描结果另存 `reports/corporate_actions_completion_v1/`，以最终回执为准。

### Notes
- 公告语义是来源绑定审核声明，非自动法律/文档语义认证；历史状态可见时点、成本及送股税分配仍有明确模型说明。没有策略盈利资格或Paper资格声明。
- 全池价格/公司行动条款覆盖不等于整池账户通过。碎股、登记后权益变化、配股及未知退市结算仍明确阻断，目标名单及旧研究预算未缩小/重置。
- 本次从main f0b5bde创建隔离分支 `codex/corporate-actions-completion`；PR远端验收和主目录同步由后续交付回执记录。回滚采用本次提交的git revert并切回旧登记清单；保留新旧冻结输入、原件与已消费工程预算，不能原地改写历史。

## 2026-10-02 - Task: 按数值、日期、账户条款顺序执行定点补证

### What was done
- 新增 scripts/resolve_universe_numeric_terms_v1.py，复验既有物理限窗通达信包、原BaoStock行与数值；48项中47项形成来源绑定零现金回执，301019.SZ/20240711比例问题保留。可选送转率空白仅沿用既有SHARE_RATE_POLICY，现金空白仍需独立证据。
- scripts/prepare_universe_actions_v2.py 新增 --numeric-terms 消费重算回执，原件空值保留；新增 scripts/classify_universe_action_evidence_v1.py 对30项分类，25项有同日TDX记录，9项涉及特殊重整，30项均未被自动清除。
- 新增 scripts/fetch_universe_action_announcements_v1.py，要求冻结完整发行人orgId、精确原件SHA及公告窗口；保留PDF/TXT/请求/哈希和UNREVIEWED状态。修复同义实施公告漏选、文本落盘后崩溃恢复，以及缺orgId成功空响应误报缺公告。
- 已查询676项冻结目标（645账户条款、30日期、1数值冲突），先取得651个目标/666份PDF。旧标题漏选14项只在新版本重采；HTTP 504失败原样保留并单目标重试。最终聚合数量以 reports/targeted_action_evidence_v2/FINAL_EVIDENCE_INDEX.json 为准；2份图片PDF无可提取文本单列。
- 实际全文及独立来源审查后接通001298/20240529、002738/20230531、300586/20230426、300700/20230404四项完整股份条款；000039/20220818只接通有明文的到账/交易日期，税源仍UNKNOWN。没有批量把下载成功升级为条款审核通过。
- 最终准备清单 manifest_actions_targeted_v3.json：4,607只目标保留、7,121事件、47项空现金解决；价格资料登记覆盖4,578只、公司行动账户条款覆盖4,028只。日期缺口30、数值缺口1、账户条款缺口641（640通常未知、1明确税源未知）。这不是整池账户或策略验收。
- 维护 docs/CORPORATE_ACTIONS_V2.md、docs/TARGETED_ACTION_EVIDENCE_V1.md、Windows/Linux full-universe CI及四个测试文件；.gitattributes只固定三个新增算法LF字节，其他源码编码不变。旧回执、失败、旧算法精确快照和原件保留，研究资料不上传。

### Testing
- 修改前相关基线34项通过；全范围回归800 passed、2 skipped（Windows符号链接权限及当时环境无pypdf），耗时924.89秒。后续全部定向最终修复227 passed（14.68秒），通过sys.path末尾追加已有bundled依赖，实际pypdf提取及中断恢复测试执行，未安装依赖或用跳过冒充通过。
- 真实numeric_terms_v3重算47 VERIFIED/1 UNKNOWN；原件SHA、行身份、TDX现金/配股/股份比率及算法版本绑定复验。最终准备manifest SHA c0ae28009efc9ae43d37049e3194bee602d8c88024ff7be1385caae0cef77a28。
- 676项旧采集的完整只读resume通过，未重复请求；修改标题算法前精确快照SHA 061c36dc1e32115b399dd69c10facfc6f79d14731974d151d814fc2856621e75 与旧READ_PLAN一致，旧结果不能用新算法原地改写。
- 四项已审股份条款构造时单独验证通过；000039因SHARE_TAX_SOURCE_UNKNOWN仍如预期阻断。未运行账户或创建预算，碎股分配拒绝保持。能力说明及公共示例generate_research_capabilities_v1.py --check MATCHED；git diff --check通过。
- ce-code-review两项独立评审发现可选空股份率、orgId、文本恢复问题，均已修复并定向复验；额外真实批次发现的标题等价问题已复验。远端发布验收另存本地 RELEASE_RECEIPT.json，不以本地测试代替CI。

### Notes
- 301019公告证明参与分派股东实际比率与全公司除权折算比率不同；当前股份价格路径未分别映射，六位小数及float32差异不靠放宽容差通过。000796重整新股不分给原股东，特殊参考价和账户分配不可按普通转增处理。
- 尚未在限窗定位的公告、图片待读、条款缺失及未审核分别保留；下载不是账户资格。原4,607只名单、已有研究次数/预算、原件和失败不删除；其他历史状态、退市结算及完整账户链路仍需验收。
- 本次在隔离分支codex/targeted-action-evidence-v2从main bb377ab推进，主目录未提交研究保持。回滚用本次发布提交的git revert并切回manifest_actions_complete_v1.json或旧登记版本；冻结身份绑定原工作区绝对路径，工作区保留用于重放。


## 2026-10-02 - Task: 审核本地公告并按字段登记送转补证

### What was done
- 原641次送转事项本地公告逐项审核，623项仍缺字段；原607项补采队列与漏掉的16项送股分别冻结，740个窄查询取得498份新PDF（496文本、2扫描件）。只补缺明文材料，不重采行情、缩小名单或补造来源。
- 原明文100项股本溢价来源、416项到账证明，新增12项来源；15项送股金额用官方税率及双档Fraction算术形成DERIVED_MODEL/CHILD_LOTS_MODELED声明。002865舍入歧义保留，未默认面值或转增免税。
- scripts/prepare_universe_actions_v2.py改为字段级登记，未提供/UNKNOWN不覆盖已有值，MODELED不降级SOURCE；已证日期缺值/源仍拒绝。新增6例回归、加入autonomous-completion选集，仅该算法固定LF以保证回执跨平台哈希。
- docs/CORPORATE_ACTIONS_V2.md及docs/SHARE_ANNOUNCEMENT_AUDIT_V3.md说明使用、模型及分层结论。本地版本化资料V6保留4607目标、7121事件、646条声明；公司行动账户条款覆盖4059，尚有603事件/519股票送转缺口及31事件/29股票价格日期问题，联合548股票。不是完整账户资格。

### Testing
- 针对性62项通过；新增模块独立6项通过。full-universe选集原882 passed/3 skipped/1环境失败；唯一失败来自E:exFAT不支持junction，HEAD基线与当前代码在C:NTFS通过。原PDF两跳过加该环境失败精确重试3 passed，未改数值库或虚构原选集全部通过。
- 默认全目录测试中断；首失败复现172 passed/1 skipped/1 failed，旧HEAD模块同样缺本地event_store工件，保留原失败。远端发布CI另行核验，不以这些本地结果代替。
- V5公共SCAN实际4607唯一股票、3713完整/362部分UNKNOWN/532 UNKNOWN；严格ACCOUNT输入预检同4607、486交易日，915局部缺口、619全局阻断、资格0，预算和账户均未执行。V6最新全池复验另存最终报告，不能用准备覆盖数代替严格ACCOUNT结果。
- V6正式字段独立复核15模型真正登记，原117已知税源、421SOURCE到账及646SOURCE上市不退回，比例/日期/来源身份不变。能力文档--check MATCHED，git diff --check通过，source diff独立评审零残余发现。

### Notes
- 计税平均基数属明文金额支持的模型推导，税权分配未获中国结算/券商认证；历史日内可见时间MODELED，历史全市场完整性UNKNOWN。未执行策略收益、模型调用、Paper或研究预算。
- 本次从main39efa02在codex/share-announcement-audit-v3推进；只提交本次源码/测试/文档/记录，原件与研究报告留本地。发布及主目录最终同步以RELEASE_RECEIPT及复制回执为准。
- 回滚点39efa02；git revert本次修正提交并切回旧登记清单，不删除原件、旧失败、冻结身份或预算历史。保留原worktree支持绝对路径复核，主目录原progress字节及他人研究保持。


## 2026-10-03 - Task: 全池检查、合格范围回测与公开排除清单

### What was done
- 新增公共 FULL_UNIVERSE_SUBMISSION_V2 / DATA_QUALIFIED 模式：先按账户所需资料检查整个登记股票池，再冻结全部合格股票，不按收益、买入信号或自选股票筛选。原 V1 严格全池入口保留。
- 资格回执绑定原全池输入、合格/排除名单、逐项缺口、日期和来源；实际执行从父输入重算资格。共同未知、空合格范围、规则/来源变化继续阻断；同日组合公司行动的账户冲突在资格检查时排除或阻断。
- 正常/压力账户沿用公共批准、预算、逐日账户、独立核验和恢复；报告及工作台公布范围与 EXCLUSIONS.csv。补齐资料后登记新版本重新检查，不覆盖旧结果。
- 更新 docs/QUALIFIED_UNIVERSE_RESEARCH.md、公共使用/能力说明和 CI 选集。研发在隔离分支 codex/qualified-universe-research；主目录原研究与未提交记录保留。

### Testing
- 公共流程、离线核验与 web 相关组合 104 passed；资格/账户/公司行动定向 138 passed、公共冻结/资格补充 22 passed。
- 新增真实 Windows worker 中断后 public resume、BASE/STRESS 报告范围绑定两项 2 passed；结果与连续执行逐日一致，原预算与启动记录字节不变。
- 前端 23 passed，vue-tsc 与 Vite 生产构建通过；能力生成 --check MATCHED，git diff --check 通过。完整 full-universe 对应选集首跑 919 passed/3 skipped/1 failed，末尾反序规模 worker 的退出失败独立重试，首失败保留。真实 4,607 目标首检查因投影内存峰值阻断；改为独立列投影及私有独占列接管，公共输入复制隔离不变，定向 25 passed。最终资源、CI 与全池证据另存发布回执，不用局部成功冒充整池账户通过。

- 真实主目录 V6 输入在原 900秒/2048MiB 限制下完成全池资格检查（837.515秒）：4,607登记、3,701合格、906排除、共同阻断0；父输入身份 e8c3804e1fe5d1699b1b08db6eb78fb75f53b72cf85b9470506e0bdf7fa31d75 未变。feature 20220801..20240731/account 20221101..20240731，未运行账户、未创建预算。
- 反序 4,536 合成规模 worker 独立复验 exit0（212.654秒，峰值653080KiB、五账户日均全目标），关键源码哈希未变，stderr为空；只证明本次参数当前源码通过，不推测旧失败原因。

- 最后补充投影 dtype 保真边界：object 普通/时区 Timestamp 等7类与原投影表、身份及完整回执精确一致；先复现两类类型推断失败再修复，最终 inputs/scope/corporate 定向 150 passed。新实现不增加整列副本，公共输入隔离与策略行为保持。
- 远端 workflow 首轮揭示原独立报告接口回归：491 passed/1 skipped/1 failed，新增范围注释误要求旧报告必须带 items。保留原测试和失败日志，修正可选报告分支判断；旧报告及新合格范围公共账户/真实中断恢复定向最终 19 passed（125.09秒），能力生成 --check MATCHED。完整远端复验以最终 HEAD 的发布回执为准。

### Notes
- 合格范围是按评价区间资料可用性确定的回顾性范围；历史可投资清单完整性、当时可见性与幸存者偏差没有升级为已验证。资料合格、账户核账、策略盈利、独立验证、Paper 资格分别报告。
- 当前任务授权固定规则工程验证及旧开发目录清理；唯一研究/原始证据先按 SHA 留恢复包，主目录行情和他人研究不删。旧绝对路径文件可以恢复，但物理签名、冻结计划和许可须重新核验；实际释放与 main 同步另记交付回执。
- 回滚点 6e300d6a337ee92b7f59d3f95725473c4fcba925；使用发布提交 git revert 或改回 V1 严格模式，保留已消费预算、失败和历史账户记录。

## 2026-10-03 - Task: 修复账户核验身份冲突并重验证同一策略

### What was done
- 修复universe_evidence_v1.py的FIFO卖出手续费分配运算顺序，保持原值身份与金额容差；增加独立重建账务失败证据。universe_account_backend_v1.py绑定daily_plan.py/common.py并明确期末失败账务范围。没有调整策略、引擎费用、均价或交易规则。
- 新增tests/research_factory/test_universe_account_identity_v1.py，更新docs/QUALIFIED_UNIVERSE_RESEARCH.md。本次交付reports/strategy_identity_fix_20261003_v1/REPORT_CN.md、FINAL_SUMMARY.json、公共原生任务、冻结对照、原材料保护和测试证据；assemble_delivery.py只汇总已完成证据，不执行账户。
- 同策略TREND_MOMENTUM_RECOVERY_50K_V1、同20240718—20240731日期、同50000元、同输入/合格范围：全池4607，执行3936，排除671；公共任务5eb6de890000f5968dd9490b43939af43df2975fa876f6c70b2831770bc11f97最终ACCOUNT_VERIFIED。正常期末49460.9367元，压力49353.42190000001元，策略仍亏损；不是独立样本或策略资格。

### Testing
- 修复前真实合成部分卖出用例2项失败；相关测试96项、新回归22项最终通过（共118，无失败/跳过），日志XML保留。实际压力成交的末位差异补成可移植合成回归。
- 公共start退出码0，两账户工作进程退出码0/未超时/结算成功，原生完整VERIFICATION均PASS；各10天现金、权益独立重建差额0.0，每日完整3936只扫描。正常账户6组经济/决策/扫描结果与前轮逐项完全相等。
- 前轮178文件、当前515运行源码、88冻结绑定、登记manifest均逐字节未变。旧预算CONSUMED2、新授权工程重验证CONSUMED2；SOURCE_BASE记录修改后未提交源码身份，未修改旧冻结任务或退款重试。

### Notes
- 准备期间最终证据序列化补齐后，旧preview被freeze正确以SUBMISSION_PREVIEW_CHANGED拒绝，未执行账户/消费账户额度；保留原记录并刷新后冻结。早期测试注入时点和测试导出字段错误仅修正测试，保留失败日志，没有削弱断言。
- 原失败无账务快照，不把本轮手续费差异回填成旧失败现场；裸账本均价诊断不适用当前后端，未改均价。压力成本改变002655.SZ止损卖出日期，不能称固定成交路径成本压力。状态可见性、流动性和成本仍为模型，共享指标公式重算非独立公式证明；Paper0、正式资格未授予、未调用原真实模型链。
- 工作树在main，源码修复未提交/推送；保留此前progress.md全部字节。回滚基点d7634c6d2e51538d0c1440d1fb5d93cc297c5ea4：审阅后仅恢复本次2个源码文件与docs/QUALIFIED_UNIVERSE_RESEARCH.md、移除本次新测试；不执行全工作树reset，不删除账户报告、授权和预算，不退款或改写CONSUMED。原始研究文件不变。

## 2026-10-03 - Task: 提交账户核验修复并准备main发布

### What was done
- 按用户明确授权提交、推送并合并本次账户核验修复；仅包含核账器、账户后端、22项新回归测试、使用说明、本次任务日志和CI的一行测试目标。当前分支codex/fix-universe-account-identity，基线d7634c6。
- 将新回归文件加入既有full-universe的Linux/Windows矩阵；其他AI可按AUTONOMOUS_RESEARCH_USER_GUIDE.md、QUALIFIED_UNIVERSE_RESEARCH.md、RESEARCH_CAPABILITIES.md通过原公共入口设计并提交V3策略，无需逐策略重写接入代码。

### Testing
- 本次修复已有118项本地测试通过，两套同策略/同日期真实账户及完整原生核验PASS，正常成本结果与前轮逐项相等。没有再次启动已消费账户。
- generate_research_capabilities_v1.py --check为MATCHED；YAML检查确认新回归准确进入Linux/Windows的full-universe目标；源码/说明/CI差异空白检查通过。远端CI、PR及最终main同步状态以reports/strategy_identity_fix_20261003_v1/release_v1/后续发布回执为准，本记录不预先声明远端完成。

### Notes
- 修改文件：src/chanlun_trader/research_factory/universe_evidence_v1.py（费用顺序和失败重建证据）；universe_account_backend_v1.py（源码绑定和期末失败证据）；tests/research_factory/test_universe_account_identity_v1.py（22项回归）；docs/QUALIFIED_UNIVERSE_RESEARCH.md（核验及重验证说明）；.github/workflows/autonomous-completion.yml（加入本回归）；progress.md（本两项任务追加）。本次发布资料仅在本机release_v1保存，不推送市场数据/账户原件。
- 之前未提交的研究资料和历史progress内容保持原字节；索引日志仅包含已提交历史与本次两个任务，不把其他待提交研究记录混入修复。发布后在原主目录同步main，不创建额外开发目录。
- 可回滚到d7634c6，合并后对本PR的合并提交执行git revert -m 1 <merge_sha>（提交方式以发布回执为准）；保留全部账户/失败/授权/预算档案，不退款CONSUMED或修改冻结任务。账户核验通过不代表策略获正式资格，Paper仍0。

## 2026-10-03 - Task: 执行全池长期研究计划与真实规模验收（进行中）

### What was done
- 按用户指定 ce-work 执行 docs/plans/2026-10-03-001-feat-long-horizon-universe-research-plan.md；在隔离分支 codex/long-horizon-universe-research 开发，基线 786de0d307b770b6ac8f9a99e4349a588ed90ca1。主目录中其他 AI 的修改、研究记录和活跃任务保持原样。
- 新增 FULL_UNIVERSE_SUBMISSION_V3、RESEARCH_RULE_STRATEGY_V4 与 UNIVERSE_ACCOUNT_BACKEND_V2：全登记检查、全合格扫描、固定评分、分段累计资源、完整状态恢复、独立核账、数量原因漏斗、账户与信号双报告；准备/核验/报告各自受治理，不以账户超时放宽其他阶段。
- 公共 CLI/API/工作台展示资源、评分、状态及暂停恢复；更新能力目录和中文使用说明。新增原生 BaoStock RAW 接入及长期准备程序，不伪造旧格式来源，不改写旧正式数据资格。
- 为实际 U8 冻结全部 4607 只登记证券的 9214 个原件请求，采集目录为主目录 data/long_horizon_universe_v1。252/504 账户及对照将保存在主目录 reports/long_horizon_universe_acceptance_v1；目前采集仍进行，未声称真实 504 验收完成。

### Testing
- 专项已通过：评分与旧策略兼容 53 项；新数据/日期相关 66 项；账户与报告/漏斗相关 136 项，最终报告续跑 58 项；公共新入口/治理专项 83 项。前端 27 项、构建、工作台/API 24 项通过，真实只读浏览器验收证据保存在上述主目录 ui/。
- 中断资源门新增 6 项，区分实际测量与保守上界收费；与特征分片 4 项合计 10 项通过。完整测试及独立审查正在执行，发现的失败与缺陷需修正后再次核对，不能据专项通过替代全计划验收。
- 实际 252/504 全池六账户验收、远端 Windows/Linux CI、推送合并及主目录同步尚未完成；最终状态将追加真实证据。

### Notes
- 修改涉及 research_factory 的新版本后端/规则/状态/报告及治理接线、公共脚本、webapp、工作台、专项测试、CI 与 docs；不增加 ATR 止损、市场指数上下文、分钟/实盘或正式统计资格。
- 历史状态可见时间仍标 MODELED；全窗口 DATA_QUALIFIED 不是完整历史可投资市场证明。信号观察不是实际账户收益，工程验收允许策略亏损，不领取正式资格或 Paper 天数。
- 回滚点为 786de0d；未合并时仅恢复本分支自有代码，合并后对本 PR 合并提交执行 git revert。保留授权、预算、失败与原始数据/账户证据，不退款 CONSUMED，不删除其他 AI 文件。交付后仅归档本次开发工作区。

## 2026-10-03 - Task: 修正真实全池验收的重复核验和 Windows 临时目录兼容

### What was done
- 保留真实 U8 的前两次准备失败及第三次账户启动失败；第三次完整检查 4607 只，252 日范围合格 3669、排除 938。账户未提交任何交易日便在 2048.75 MiB 失败，原 START 已消费，未退款、未重开原失败用途。
- strategy_submission_v1.py 提前释放两份 JSON 和重复父快照；完整父资格重算及正式子包三表物理/类型/逻辑校验不变。universe_qualified_scope_v1.py 与 universe_account_inputs_v1.py 只在同进程、同对象、同区间和同要求下复用刚严格认证的子输入，复用前仍核对身份、回执和未变更性；公共构造器仍完整认证。
- universe_evidence_v2.py 仅规范化系统自建临时目录的实际根；显式传入路径的重定向检查保留。新增严格复用与路径反例，加入 Linux/Windows long-horizon 矩阵；npm ci 禁止自动生命周期脚本，并通过现有界面测试与构建。
- 源码冻结到主目录 reports/long_horizon_universe_acceptance_v1/source_archive_v5，重新登记 u8_v4 工程配置；同策略、同资金、同日期和全登记分母。旧失败与已收费证据由 ENGINEERING_ATTEMPT_HISTORY.json 绑定，六账户真实验收已启动，尚未声明完成。

### Testing
- 严格资格、所有权与篡改拒绝：66 个唯一用例通过；另两个公共账户/恢复集成通过。独立审计临时目录 16 项通过，包含私有别名可用而显式重定向仍拒绝。前端 27 项与正式构建通过；能力目录 --check 为 MATCHED。
- 受限真实诊断第一次：完整父派生及子还原 552.0626463 秒，重复后端严格准备 188.7693893 秒，总 741.088725 秒，峰值 1959.5703125 MiB。第二次：560.2402322 秒与 8.2370193 秒，总 568.5711978 秒，峰值 1962.3984375 MiB。两次均为只读诊断，退出码 0、上限 900 秒/2048 MiB/单数值线程，未启动账户、未改变原预算；原件保存在主目录 qualified_loader_diagnostic_v1/v2。
- 新增静态正确性复审无可确认缺陷；真实六账户、504 日三处暂停、连续逐日对照、完整公共核验及当前提交远端 CI 仍待实际证据。

### Notes
- 此处复用的是同进程刚认证的行情及资格输入，不复用交易或独立核账答案，不把已用历史称为独立验证，不授予策略资格或 Paper 天数。跨进程仍重算完整父资格。
- 39268aa 的 Windows long-horizon 13 项失败均为默认临时路径提前拒绝；修复后的远端结果不得由本地通过替代。外部安全扫描的路径/CLI 告警另按真实调用边界逐项审阅，不删除保护或关闭扫描，不预先声明其状态已转为通过。
- 回滚点仍为 786de0d；合并后 git revert 对应合并提交。保留全部原始数据、源码归档、失败、授权、预算及已消费记录；不清理其他 AI 的主目录改动。

## 2026-10-04 - Task: 对齐真实资格投影与只读发布身份

### What was done
- long_horizon_acceptance_publication_v1.py 修正裸投影身份与含资格回执的最终身份被错误要求相等的问题；两者必须是合法不同 SHA，完整子窗口必须等于父窗口仅替换合格证券。冻结 INPUT/JOB、准备输出精确采用、独立 PASS 与源码门禁保持。
- 修正合成发布夹具并加入真实三板块 production qualifier 与身份/窗口篡改回归。发布器不读取行情或重算账户；当前真实账户 JOB 不绑定发布器，其执行源码闭包未被该补丁改变。

### Testing
- tests/research_factory/test_long_horizon_publication_v1.py：38 passed in 36.55s，XML 与 JOB 来源核对回执保留在主目录 reports/long_horizon_universe_acceptance_v1/publication_scope_review。
- 独立只读正确性审阅无可确认缺陷；git diff --check 通过。最新49f3f6b的 Windows/Linux long-horizon 与 full-universe CI 已通过，Windows workflow/formal 仍运行，外部 SonarCloud 状态未声明通过。

### Notes
- 真实 u8_v4 已完成252日全池资格检查，但首个账户在已知停牌的除息日价格准备失败：610.6213124秒、1977.72265625 MiB，未提交首个交易日。原 START 仍已消费且 FAILED，不退款、不重开；新版停牌因果价格接口正在另行修复，六账户验收尚未完成。
- 回滚点仍为786de0d；只回滚本任务两文件补丁，不删除原始账户、资格、授权、计费与失败证据。

## 2026-10-04 - Task: 修复长期全池停牌除息价格与账户估值
### What was done
- 真实 U8 第四次工程验证的第一账户在首个账户日以前因 CAUSAL_PRICE_EX_DATE_MISSING 失败；已消费用途及实际 610.6213124 秒完整保留，不重开失败任务。全合格池取证定位四只证券的来源明确停牌区段，并保留真实日历、状态及分红原件哈希。
- 新长期扫描和独立核验使用 CAUSAL_SUSPENDED_CASH_AND_SHARES_V3：按真实停牌缺口、条款及官方复牌昨收衔接价格，不补造行情。V1/V2 旧价格函数和旧任务不变。新账户及独立核账各自修正停牌现金除息估值，并显式登记 MODELED_SUSPENDED_EX_REFERENCE_V3；未来复牌价格不用于提前估值。
- 增加价格、现金与送转、连续事件、恢复、开盘资金分配及双报告回归；同步公共能力和中文使用说明，并纳入长期 CI。
### Testing
- 修复前账户估值两个回归失败；修复后四组分别 85 passed/39.38s、38 passed/37.89s、3 passed/7.84s、10 passed/7.38s。组间有重叠，不能合并称为独立总数。额外对抗审阅 findings=[]；提示的集成覆盖缺口已补测。
- generate_research_capabilities_v1.py --check 返回 MATCHED；固定多指标规则身份 c7634f41…仍一致。
- 有界真实诊断已遍历 3669 合格证券，最终诊断计数断言失败：价格规则标签被误当为停牌案例。590.5079429 秒、1967.98828125 MiB、零账户启动的原失败记录保留，修正诊断口径后以独立新目录重做；本条不宣称真实 U8 完成。
### Notes
- 账户停牌估值仍为 MODELED，不升级历史可见性、策略资格或 Paper。所有旧失败用途、原始资料、股票分母、日期及费用边界保留。
- 真实证据位于主目录 reports/long_horizon_universe_acceptance_v1/sparse_cash_review 与 sparse_ex_date_diagnosis；完成下一轮六账户与独立核对之前保持计划 active 和 PR draft。
- 本次仅新增/修改长期源文件、相关测试、工作流、说明与本记录；可按本次提交 git revert 回滚，不能删除旧失败原件或改写旧账。

## 2026-10-04 - Task: 修复长期共享截止与独立指标分段核验

### What was done
- 真实 u8_v5 在原900秒登记边界内另乘 .8，严格加载约570秒后账户推进窗口仅约150秒；前四段实测表明252日累计额度存在可预见不足。经公共 pause 安全暂停至20240221，六段4251.2089379秒原消费与状态完整保留，未重开或退款。
- run_strategy_account_v1.py 以受限 worker 启动时钟和已核验 HANDSHAKE 的890秒硬额统一截止，固定60秒收尾；账户、公共prepare、尾部核账、核验及报告共享剩余时间。独立核验每段最多新增一个成员冷加载；原派发不足最低收尾余量时明确失败，避免空转。旧入口与连续对照账户无合作截止的行为保持。
- research_evidence_v1.py 仅给新长期受管调用接入可选绝对截止，扣除内部严格读取时间；universe_evidence_v2.py 增加己方逐股特征提交与完整源码/区间/表形状/数值哈希核对，恢复只用自己的已认证前缀，不读取执行器特征或重开计时，不改变最终数学和扫描身份。
- 新定向回归接入 Windows/Linux long-horizon 矩阵，更新中文运行说明及同源能力文档。重新冻结源码并以同规则、5万元、4607登记分母、原日期和原限额进行六账户工程验证；本条不声称真实U8完成。

### Testing
- 修复前共享截止回归11失败/2通过，短原额度反例2失败；独立特征分段反例1失败，红绿原件均保留。root整合回归 181 项，失败0、错误0；各专项存在重叠，不合并称独立总数。
- 具体XML及源码修复摘要保留在主目录 reports/long_horizon_universe_acceptance_v1/shared_deadline_review；真实252/504、连续状态对照、当前提交CI和合并交付尚待完成。

### Notes
- 不扩大900秒、2048MiB、单线程、4/8小时累计规格，不改策略、窗口、数据资格、成本或账本数学，不把模拟历史可见性升级为真实证据。
- 旧SRC和暂停用途原样留存，新工程验证需要新SOURCE与授权身份；累计报告包含旧失败、此次暂停和最后验证所有实际耗时。源码修复可按本次提交git revert回滚，原账、授权和CONSUMED记录不得删除或退款。

## 2026-10-04 - Task: 修复长期独立核验的拒单原因优先级

### What was done
- 真实 u8_v6 的252日BASE已通过内核独立核账；STRESS完整执行252日，但核账在20241101（第210日）阻断。601568.SH买入7500股的模型金额31788.75元，当前组合额度允许，超过前收权益63516.7875元的50%上限31758.39375元；执行器正确返回MAX_POSITION_WEIGHT，核验器错误归入组合限制。真实诊断13项核对及SOURCE7、两日分片身份保留在主目录 broker_priority_review。
- universe_evidence_v1.py仅在UNIVERSE_EVIDENCE_V2路径分开组合限制及原有底层风险，按真实顺序核对原因和比率容差；旧V1保持原分类和金额比较，执行器、成交、资金、成本、数据和策略不变。订单两份原因字段仍须与独立算术预期精确一致，伪造原因仍拒绝。
- 新测试覆盖真实后端、组合优先、权重优先持仓数、原生持仓数拒因、比率容差和伪造拒因；接入现有Windows/Linux长期矩阵，更新中文说明。原失败保持终态，未重开或退款；新SOURCE与新工程验证仍待完成。

### Testing
- 修正夹具后的正确红版8项为7失败/1通过/0错误，绿色专项通过；root相关整合回归258项，失败0、错误0。早期设置记录另存，不当成根因复现；各组有重叠，不合并称独立测试总数。
- 全部XML/log与SOURCE_FIX_SUMMARY.json留存主目录；真实六账户工程验收、当前修复提交CI和最终合并尚待完成。本记录不授予策略资格或宣称U8已通过。

### Notes
- u8_v6原准备880.8388409999607秒、BASE和失败STRESS合计24751.98022200007秒、两用途CONSUMED和旧SOURCE原样保留，累计交付报告不得漏计。新验证仍用同一固定规则、日期、全4607登记分母、5万元与900秒/2048MiB/4或8小时限制。
- 源码回滚可git revert本次修复提交；旧账户、失败和消费原件不得删除、重写或退款。恢复旧核验会重现本次拒因误分类，不能将其当作新的通过证据。


## 2026-10-07 - Task: 恢复断盘后原长期工程验收宿主

### What was done
- 本机原E盘恢复后，核对SOURCE8全部540份源码与899dcbc已提交版本、三份原授权及原输入；252日BASE已完成并独立核账通过，STRESS保留156日收盘完整检查点。原宿主及第9段worker/reader均已退出，第9段无终态失败或资源回执。
- 通过原公共验收脚本恢复同一u8_v7任务，公共resume将未知第9段按派发上限900秒保守扣账，再派发第10段；保留原START、授权、消费、规则、日期、全4607登记分母和5万元，不新增账户用途，不重跑已完成BASE，不修改冻结源码。
- 原HOST_START、原日志及新HOST_RESUME_001、段级RESUME/CHARGE和外部恢复helper原件保留在主目录u8_v7目录。仍需完成压力账户、独立核验/报告、504日真实暂停及连续对照、最终发布/合并/同步/清理。

### Testing
- 恢复前只读核对全部540份冻结及工作区源码字节、原专项及258项整合XML、原数据/日期/授权配置和未结算消费；恢复后现场核对第9段UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND=900、budget_reused=true及第10段charged_before=7605.180220599956秒。
- 原252日BASE的独立PASS和已完成结算保持，当前Win/Linux必需CI通过；本条不声明六账户验收完成，也不称为新增策略有效性证据。

### Notes
- 原额度为每252日账户14400秒、worker900秒、2048MiB和单线程，绝对授权仍于2026-10-10到期；停机等待不重置预算。未知段900秒属于保守扣账，不能写成实测耗时或虚构其内存峰值。
- 必要时使用原公共pause安全暂停；不得删除/回滚原账、消费或未知段扣账。源码回滚点仍为899dcbc及其前序提交，真实工程通过后再合并main并保留主目录其他AI未提交内容。


## 2026-10-07 - Task: 同步当前源码的CI与静态扫描审阅证据

### What was done
- security_review/SONAR_REVIEW_CN.md增加899dcbc实际后续结果，保留39268aa旧失败和12项历史告警；同目录保留官方CI_COMPLETION_899dcbc.json及SONAR_CURRENT_HEAD_REVIEW_899dcbc.json原字节。
- 如实区分必需Win/Linux八组通过、PR十工作流成功与SonarCloud仍失败/11项OPEN；未改源码、外部告警状态或安全门禁。

### Testing
- 核对两份官方快照SHA256、相同实际源码HEAD、8项必需矩阵全成功及11个告警key；不重新执行已通过测试，不将源码CI当作真实U8或策略资格。

### Notes
- 最终元数据提交需要其实际HEAD的新CI。可revert最终文档提交撤回本说明，但旧外部失败、真实账户及消费原件不能改写。


## 2026-10-07 - Task: 修复全池长期报告的重复读取并验证原资源上限

### What was done
- u8_v7两个252日账户均完成且逐日独立核账通过，统一VERIFICATION通过；REPORT第四段在最后排版重复读取全市场行情时MemoryError，峰值2049.44921875MiB。原失败、SOURCE8、两用途CONSUMED、原恢复未知段900秒保守收费全部保留，未重开或退款。
- run_strategy_account_v1.py最后排版复用当前受限进程中已严格准备的输入，绑定原输入、全部日期、资金、登记规格和结果结算；universe_benchmark_v1.py复用同一已准备输入，价格篮子沿用原SCAN默认条件，策略字段/100日预热不改变对照名单。最终排版共享原进程截止，超时只留下继续状态，文件存在不当成成功。
- 新回归覆盖重复加载、输入/窗口/资金/规格冲突、数据变更、结算冲突、最终截止及原公共调用。恢复测试先证明实际Windows reader退出，再断言恢复、失败和收费规则；没有修改生产存活检测。中文说明同步更新。
- .gitattributes仅为实际作业91个Python源码闭包中缺失的58项追加原有CRLF检出声明；540个源码原字节全部未变，保障跨Windows/Linux发布核对；没有转换或修改这些源码文件。

### Testing
- 正确根因红版1失败/0错误，另有预热归属反例1失败/0错误；最终专项25项及相关整合131项全部通过。早期夹具、导入、运行中源码变更及Windows退出时序失败分别留存，不能称为绿色证据，各组重叠不相加。
- 新目录有界真实全池报告诊断：登记4607只、合格3669只、同一252日、同一5万元、原两份闭合账户只读；两报告成功生成，实测643.1040193999997秒、Windows Job峰值1965.8671875MiB，未启动账户。当前源码和原账户/失败/预算SHA256均在前后核对一致。证据保存在主目录report_input_reuse_review及report_layout_diagnostic_v1。
- 当前修复提交CI、新冻结SOURCE和真实六账户验收待继续；报告诊断不代替六账户验收，也不证明策略有效或Paper资格。

### Notes
- u8_v7准备891.4695516999927秒；两账户实测24738.8067214秒另含未知段保守900秒，共收费25638.8067214秒；核验1136.4779247999995秒、含失败的报告2904.8842165000024秒全部保留。新工程验证使用独立用途，继承历史记录，不重置原额度。
- 固定规则、日期、4607登记分母、5万元、900秒/2048MiB和4或8小时限制保持原计划；最终仍需252/504分段及504连续对照六账户、真实暂停恢复、源码/结果闭合与当前提交CI。
- 可git revert本修复提交撤回运行逻辑和文档/属性声明；原SOURCE8、账户、消费和失败原件不能删除或改写，恢复旧逻辑会重现报告重复读取。

## 2026-10-07 - Task: 保留报告修复提交的完整远端CI结果

### What was done
- 当前修复提交f36c6add1fe3c5c0d715f7a95ac536ba67179661的真实远端结果已保存到主目录report_input_reuse_review/CI_COMPLETION_f36c6ad.json，并保留官方PR、八组作业、十工作流和全部检查原件。
- 真实六账户仍在u8_v8按原范围与限制运行；没有将云端通过冒充真实数据验收、合并完成或策略资格。

### Testing
- GitHub官方run 37575448667的Windows/Linux八组全成功；同一HEAD的PR十工作流和25项PR检查均成功，必需Deterministic governance suite成功。
- SonarCloud服务器仍为FAILURE，已有同一HEAD的11项OPEN人工审阅原件；没有修改外部告警状态、忽略失败或将取消的重复push算作成功。

### Notes
- CI完成回执SHA256为483bbc6c0bfc0d02369050ad7f10639815be3d1d4bb98acc75a22c6f9be39e2d；最终元数据提交仍需对应最终HEAD的CI，完成六账户后才进行发布、合并与安全同步。
- 可revert后续文档提交撤回本进度说明；此操作不改变已执行测试、原失败记录、原预算和主目录保留的官方证据。

## 2026-10-07 - Task: 验证修复后真实252日公共研究链路

### What was done
- SOURCE9/f36c6ad的u8_v8完成登记4607只、合格3669只、排除938只、5万元、同一252日期的正常与压力成本账户；两份正式结果、独立逐日账本、统一VERIFICATION和四段REPORT全部通过。
- 使用实际SOURCE9的UniverseComputeGovernanceV1.status及proven_compute_result只读核对原授权、一次性预算、分段收费、受限进程和结果哈希；报告与信号漏斗文件哈希一致。原u8_v7失败及消费原件保持不变。
- 主目录保留REAL_SOURCE9_252_PIPELINE_PASS_20261007.json，SHA256为059eab85d71bccfbab3666dfc477c62dd6be927b183193d076b6f2643709f11a；剩余504日分段和连续对照仍运行，完整六账户尚未完成。

### Testing
- 两账户252日独立核账均通过；每个账户13段，累计实测10791.785886500005/10799.387443099982秒，峰值2014.0390625/2009.83203125MiB，全部原900秒/2048MiB限制内。
- VERIFICATION两段1127.7372137999992秒、峰值2006.859375MiB；REPORT四段2967.3719100000017秒、峰值2018.2109375MiB，原收费链、成功进程和成员结果均由实际公共源码核验通过。真实链路证明此前最终报告重复加载修复有效。

### Notes
- 当前完成2/6个账户验收；没有宣布U8完成、策略有效、正式或Paper资格，也未提前合并。源码不再修改，继续原固定规则与日期、504日三个暂停点和连续运行对照。
- 可revert后续元数据/文档提交撤回本说明；已成功或失败的原账户、授权、预算、收费和SOURCE原件均应保留，不重开、不退款。

## 2026-10-07 - Task: 恢复SOURCE9原504日公共验收宿主

### What was done
- 核对原SOURCE9全部540份源码、f36c6ad提交、原授权及504日输入；原252日正常/压力账户与完整核验/报告已通过且原件未变。原宿主、第6段worker及reader均已退出，504日正常账户保留70日完整收盘检查点，未结算。
- 通过原公共验收脚本恢复同一u8_v8任务；原公共resume将未知第6段按派发上限900秒保守扣账，再派发第7段。保留原START、用途、授权、消费、固定规则、全部4607登记池、日期及5万元，没有修改冻结源码、补造终态或新建账户用途。
- 本次恢复helper、HOST_RESUME_001、原检查点哈希、已完成252结果哈希及运行日志保留在主目录u8_v8。原SOURCE8失败、SOURCE9原HOST_START与原日志全部保留。

### Testing
- 恢复前只读核对540份工作区与冻结源码原字节、正式25项/131项测试回执、原配置和绝对授权仍有效、原预算消费及未完成状态。
- 恢复后实际第6段CHARGE为UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND=900；第7段charged_before=5067.378539200006秒，原总上限及一次消费未变，真实宿主、公共CLI与受限worker继续执行。
- 当前完成2/6账户；后续504日正常/压力、三个实际暂停点及连续对照、发布、最终CI、合并同步与清理仍待完成。

### Notes
- 未知段900秒属于保守收费，不能写成实测耗时或虚构峰值。宿主退出原因尚无证据，不把它归因为策略失败；原worker900秒、2048MiB、单线程和每504日用途8小时保持，授权仍于2026-10-10到期。
- 可使用原公共pause安全暂停；不得回滚预算、删除原记录或重开已消费用途。可revert后续文档提交撤回此说明，原已执行账户和收费证据须保留。
## 2026-10-08 - Task: 修复Windows观察进度时的原子收盘保存冲突

### What was done
- SOURCE9/u8_v8的252日正常/压力公共链路完整通过；504日正常用途提交192日后，在保存下一收盘时发生WinError 5，原子替换被读取进度的临时句柄拒绝。原用途结算FAILED、全部消费和未知第6段900秒保守收费保留，不自动重跑或退款。
- universe_execution_state_v2.py保留同目录完整临时文件、flush/fsync及原子替换；仅对Windows访问/共享错误5、32、33最多等待1秒，重试同一临时文件。其他错误立即抛出，持续拒绝原样失败，旧收盘始终保持完整；没有调整状态格式、策略、账户或900秒/2048MiB资源上限。
- 新增test_universe_checkpoint_atomic_replace_v1.py，覆盖真实Windows读句柄释放、三类短暂共享错误、持续拒绝的有界失败、非共享错误、旧内容与临时文件处理；加入Windows/Linux长期CI。LONG_HORIZON_UNIVERSE_RESEARCH_GUIDE.md同步说明。
- 原件及辅助诊断留在主目录checkpoint_replace_review和checkpoint_replace_diagnostic_v1；失败汇总V2按原COMPUTE收费链补全准备费，初版漏计准备的汇总原件保留但不用于后续登记。

### Testing
- 实际Windows读句柄反例在未修复源码上1失败/0错误，准确复现WinError 5；修复后11项专项和104项相关整合全部通过，两组有重叠不相加。覆盖完整状态恢复、独立账本、分段资源/截止、失败不可重开及发布身份。
- E盘有界真实快照诊断：5269894字节、192日、20次实际WinError 5短暂占用后均成功提交同字节副本；持续句柄占用仍失败，旧账户、失败与预算哈希前后不变。Windows Job实测8.268766799999867秒、峰值139.22265625MiB，0退出且未超时，没有启动账户。
- 聚焦审查重试白名单、截止和原子性、永久错误传播与原资源边界；git diff --check通过。本条不声明同一新源码的六账户已完成；当前修复提交CI及SOURCE10真实验收仍需继续。

### Notes
- 原u8_v8三账户用途消费：账户收费32282.003140300003秒，其中未知段900秒属保守收费；准备1818.3710151999949秒、统一核验1127.7372137999992秒、报告2967.3719100000017秒全部保留。失败汇总V2 SHA256为da7dd08f2d78b5dd8d222173731c94f51ce8a2cdef061f8a6acbc804ae407771。
- 后续新源码仅修复上述保存点，使用新工程用途完成同规则、日期、4607登记池、5万元及原限制的完整六账户；不能将旧252结果改标为新源码。原绝对授权仍于2026-10-10到期。
- 可git revert本次保存修复及CI/说明提交恢复旧逻辑；原SOURCE9、已通过或失败账户、消费及诊断原件应保留。旧逻辑在Windows读句柄冲突下可能再次终结任务。
## 2026-10-08 - Task: 冻结SOURCE10并启动同范围六账户验收

### What was done
- 原子保存修复已提交并推送1928c3964c004f17191c330e30cc03f0b9e24b06，PR #36保持draft。冻结SOURCE10全部540份实际源码，相对SOURCE9仅universe_execution_state_v2.py发生原子保存修复；正式专项/整合、E盘实际读锁诊断和原失败V2均按哈希绑定。
- 新u8_v9登记与原u8_v8完全一致的策略身份、日期、4607登记分母、5万元、成本、资源和2026-10-10绝对授权，只使用新工程用途。保留之前10个已消费账户用途、1800秒未知段保守收费、全部原失败/暂停及已通过252结果；没有改写旧记录。
- 独立隐藏宿主于2026-10-08 00:15:21北京时间启动原公共六账户验收脚本，PID20948；源码清单SHA256为357a8b0fd0817c59f099f992620fca12cf8befbceb1c0cb324c5a4b21819f4dc。原件在主目录source_archive_v10及u8_v9，后续继续逐日核账、三个暂停点和连续对照。

### Testing
- 启动前严格核对540份源码、已提交状态、原失败及阶段收费链、11项专项/104项整合XML、20次真实读锁提交和持续拒绝诊断、固定规则/数据/授权一致及原到期时间仍有效。未使用旧SOURCE9的252结果充当新源码验收。
- 当前HEAD的10个PR工作流已经触发；仅取消同HEAD四个重复push运行，保留全部对应PR运行和取消回执。正式Windows/Linux八组及必需检查待完成，不将取消视为成功。

### Notes
- 修复凭证SOURCE_FIX_SUMMARY SHA256为8c9a291c1def738bcf8e16fb7ad3e287fd882299d6ccaaad20c2ce61f2cb685b；这只是修复证据，完整新六账户仍运行中，不能宣布U8或策略资格通过。
- 最终同步helper已改为核对SOURCE10，仅在最终合并后执行；目前没有改动主目录业务源码或外来研究材料。必要时可用原公共pause安全暂停，不能删除原消费或重开失败任务。
## 2026-10-08 - Task: 保留1928c39官方安全扫描及定点复核

### What was done
- 主目录checkpoint_replace_review保留同一HEAD的官方PR分析时间、11条OPEN告警、质量门禁及GitHub检查原件，工作区security_review同步人工复核JSON和中文说明；没有修改业务源码、外部告警状态或历史说明。
- 对全部既有告警的位置、规则及四份实际源码逐项与SOURCE9/上一提交核对，没有新增告警或位置变化。新原子保存修复未增加路径来源、外部输入或命令执行。

### Testing
- 官方检查绑定1928c3964c004f17191c330e30cc03f0b9e24b06；11条告警全部仍为OPEN，失败条件仍为new_security_rating。人工凭证SHA256为4c8969f552a7a6eda7a6ec91c24d0c74cd73858f496fceb0bd4c26742f2ac429。
- 本次只读复核未重跑测试或账户；完整八组远端CI、SOURCE10六账户及交付仍待完成，未声称服务端安全门禁通过。

### Notes
- 同目录SONAR_CURRENT_HEAD_REVIEW_1928c39.json说明证据范围，官方原件留在主目录不依赖临时目录。可revert后续文档提交撤回说明；原官方状态及不可变凭证不改写。

## 2026-10-08 - Task: 保留1928c39远端八组及必需CI通过结果

### What was done
- 直接取得官方同一HEAD的八组Windows/Linux矩阵、十个PR工作流、全部检查原件；主目录checkpoint_replace_review保留原始JSON，工作区security_review同步CI_COMPLETION_1928c39.json及中文后续说明。
- 未修改业务源码、冻结SOURCE10、运行中的账户、预算或外部检查状态；完整六账户验收继续按原任务运行。

### Testing
- 1928c3964c004f17191c330e30cc03f0b9e24b06的八组、十工作流、25个PR检查均实际成功，包含必需Deterministic governance suite；凭证SHA256为3b7cc979dc5c8b3673246d7b2fcd0e98bc3bc6a9ecec0423bb17e37f50b88230。
- SonarCloud仍FAILURE/11条OPEN，独立复核没有新增告警；取消的重复push未冒充通过。本条未重复本地测试，也不以自动测试替代真实六账户核账及恢复对照。

### Notes
- 官方矩阵运行37650072626；当前修复提交CI完成，但U8、最终元数据HEAD的CI、合并同步及清理仍待完成。可revert后续文档提交撤回说明，原官方回执、消费和实测证据保持。

## 2026-10-08 - Task: SOURCE10首个真实252日账户独立核账通过

### What was done
- 新u8_v9的LONG_U252_BASE完整保存252个交易日，独立重建3669只股票的指标与252日账本，原结算COMPLETED、结果哈希绑定及逐日对账通过。正常账户结束后，原公共宿主继续同日期、同范围的STRESS，没有重复启动BASE。
- 主目录checkpoint_replace_review保留REAL_SOURCE10_FIRST_252_ACCOUNT_PASS_20261008.json，逐项绑定SOURCE10清单、原JOB、账户结果、结算、独立审计及13个真实资源回执；不是旧SOURCE9结果改标。

### Testing
- 正常账户实际13段：前12段合作提交退出75，最后一段0；实测及收费10825.195693800015秒、峰值2013.25MiB。全部Windows Job受限、未超时，每段不超过900秒/2048MiB，单用途低于4小时，未知段收费为0。
- 源码清单绑定1928c39及540份原件，初始资金50000、全部3669合格股票、独立逐日核验252日PASS；正式资格与独立确认资格均保持false。首账户凭证SHA256为d9077fee6aeb2f518a1d9b4966470df1bc9ca18323b2d74d5a96811da4d665ea。

### Notes
- 只完成新源码1/6账户；统一核验/报告、剩余五账户、实际暂停恢复对照和最终发布仍待完成，不标记计划completed或取得策略资格。
- 可使用原公共pause安全暂停压力账户；已完成结果、原失败及全部收费保持不变。可revert后续文档提交撤回说明，不能删除原用途或退款。

## 2026-10-08 - Task: 本机重启后恢复SOURCE10原压力账户独立核账

### What was done
- 实际核对系统最后启动时间为北京时间2026-10-08 08:26:02.5，原宿主、第13段worker及reader均已退出，缺少该段终态回执。压力账户已保存252日工程状态和137日独立核账状态，正常账户的完整结果、结算及审计哈希未变。
- 恢复前核对SOURCE10及工作区全部540份原字节、1928c39提交、固定规则/窗口/4607登记范围/5万元、原修复验证、绝对授权仍有效、原三项消费保持CONSUMED、首个账户证明及检查点。
- 通过原公共验收脚本恢复同一u8_v9，隐藏宿主于08:34启动。主目录保留HOST_RESUME_001、原检查点/预算哈希、helper原件与日志；未修改业务源码、冻结SOURCE10、旧失败或新建账户用途。

### Testing
- 原公共resume实际将第13段追加UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND=900秒，再派发第14段，charged_before=10932.077706300013。原START、策略身份、一次消费、900秒/2048MiB及252日单用途4小时上限保持。
- 原正常账户1/6通过证据未变；压力核账从原137日接续，后续结算、统一核验/报告、504日暂停恢复和连续对照仍待完成。

### Notes
- 未知段900秒为保守收费，不是实测时长或峰值；原第13段没有补造RESOURCE或成功回执。ENGINEERING_ATTEMPT_HISTORY原先的1800秒描述旧尝试，当前新增900秒由当前真实收费链保存，不改写历史。
- 可使用原公共pause安全暂停；绝对授权仍于2026-10-10到期，不能退款、重置累计时间或重开FAILED用途。可revert后续文档提交撤回说明，原恢复和收费证据保持。

## 2026-10-08 - Task: 在持久目录恢复只读验收监控

### What was done
- 发现原临时编排目录的.py文件已被清理，仅存打开的日志及缓存目录，清理来源没有证据。恢复中的SOURCE10公共宿主、worker仍在运行，主目录原SOURCE10、账户、预算和恢复helper原件保留。
- 将只读进度监控保存在主目录reports/long_horizon_universe_acceptance_v1/host_support_v1/monitor_u8_v9.py，读取原小型回执、当前检查点及资源/收费链，准备阶段沿实际COMPUTE_PREPARATION回执统计。

### Testing
- 新监控实际读取原正常账户252日PASS和压力账户137日独立核账进度；压力原用途已包含900秒未知段保守收费，HOST_RESUME_001尚未退出。核对原宿主及第14段进程均存在，没有新建账户或修改消费。
- 仅监控文件位于研究报告目录，未改项目业务源码、冻结源码、原参数、账户或统计门槛，也未重复本地测试。

### Notes
- 临时目录不再作为后续监控的必需位置。主目录原运行回执和SOURCE10源码为持续验证依据；最终同步前仍需重新准备并核对安全同步辅助程序。
- 可移除本次只读监控恢复旧观察方式；原账户、消费和全部证据不得删除，后续文档提交可revert撤回本说明。

## 2026-10-08 - Task: SOURCE10真实252日公共链路完整通过

### What was done
- u8_v9同一冻结源码的252日正常、压力账户均完成3669只合格股票的指标和252日独立核账；统一核验及两个账户的信号/账户报告全部完成。登记范围仍为4607只，938只按原资格清单排除，初始资金50000元。
- 主目录checkpoint_replace_review保留REAL_SOURCE10_252_PIPELINE_PASS_20261008.json，绑定540份SOURCE10原件、原JOB、账户/结算/独立审计、真实资源和收费回执、统一核验及报告。未启动新账户或重算已完成结果。

### Testing
- 正常账户13个实测段：10825.195693800015秒、峰值2013.25MiB；压力账户13个实测段：10845.719320500013秒、峰值2010.7109375MiB，另保留本机重启导致的第13段900秒保守收费，总收费11745.719320500013秒。两个用途均低于原4小时上限。
- 统一核验2段1121.2942515999998秒、峰值2005.58984375MiB；报告4段2976.3311959000002秒、峰值1986.81640625MiB。已知段均由真实Windows Job约束、未超时，每段不超过900秒/2048MiB；未知段没有补造实测时间或峰值。
- 完整252日凭证SHA256为1d7835c206f96406e73e074abde16e7d1bb3a16134239155140da66edac69b7e。源码仍绑定1928c39及清单357a8b0fd0817c59f099f992620fca12cf8befbceb1c0cb324c5a4b21819f4dc。

### Notes
- 新源码完成2/6账户；同一宿主已进入504日全池准备，剩余四账户、三个实际暂停点、连续对照、最终发布与交付仍待完成，不标记计划completed或取得策略/Paper资格。
- 可通过原公共pause安全暂停后续账户；已完成结果、全部旧失败与收费保持不变。可revert后续文档提交撤回本说明，不能删除证据、退款或重开FAILED用途。

## 2026-10-08 - Task: 在持久目录准备最终主目录安全同步

### What was done
- 临时编排目录被清理后，在主目录host_support_v1重建safe_main_sync_source10.py及隔离验证程序。最终同步须绑定服务器合并SHA、原main基线、工作区已提交状态、SOURCE10清单和540份原字节；默认仅检查，不现在执行同步。
- 同步只处理本次已核对哈希的未跟踪计划碰撞和progress.md；先保存外来原字节，再快进到明确合并提交，将本次纯追加记录接在外来记录后。外来未跟踪文件通过前后大小/时间核对，未知碰撞、其他已跟踪修改及身份冲突均拒绝。

### Testing
- 隔离临时Git仓库9项验证通过，包含实际快进、外来字节前缀/文件保持、计划替换、失败恢复，以及源码/清单、索引、未提交工作区、服务器身份和非追加记录拒绝。SAFE_SYNC_CHECK_20261008T021158403336Z.json绑定helper SHA256 ddacad21c144e969f808835c5d43f9b4674909c733bb9a3476ff1ed75a5f34ab。
- 主目录实际只读准备通过：SOURCE10 540份原字节未变，主目录HEAD仍786de0d，外来progress.md SHA256为6417f8a018eae0e37169c4cc8a5d6b9a0d65f101a0a308d85945f50fc8ec9b36，盘点113662个外来未跟踪项；main_mutated=false，最终合并仍待验收。
- 首轮9项断言无失败，但临时Git只读对象清理产生9个错误；原FAIL回执保留，修正隔离验证的只读对象清理后上述9项含清理全部通过。本次未改项目业务源码或启动账户。

### Notes
- 尝试清理首轮9个小型临时测试残留的递归命令被自动安全审核以blocked by policy拒绝，未给更具体原因；残留保留，不改用绕过命令，正式开发工作区仍拟经可恢复归档清理。
- 最终合并后须重新进行只读检查再明确执行；现在不改主目录业务源码、不删除外来研究材料。可删除本次辅助程序撤回同步方式，后续文档提交可revert；所有验收和失败消费证据继续保留。

## 2026-10-08 - Task: SOURCE10真实504日第一次暂停沿原用途接续

### What was done
- 同一冻结SOURCE10的504日全池准备实际完成，仍检查4607只登记股票、采用全部3669只合格股票、公开938只排除项。准备2段实测1032.904883199999秒、峰值2032.77734375MiB，未超时或新增保守收费。
- 正常成本账户第80日实际发出PAUSE控制，在第81日20230404收盘安全停下；第7段以CONTINUE/UNIVERSE_PAUSE_REQUESTED返回，原公共宿主追加RESUME控制并按原START/receipt派发第8段。
- 主目录checkpoint_replace_review保留REAL_SOURCE10_FIRST_504_PAUSE_20261008.json，将原JOB、单次消费、暂停/恢复控制链、真实资源/收费、第7段状态及第8段派发绑定，不修改原账户或消费。

### Testing
- 原START reservation保持CONSUMED，account bucket used=limit=1，暂停与恢复控制均绑定原JOB SHA和receipt；控制及派发/收费自哈希核对通过。第8段charged_before=5777.9644161999995，原504日8小时总上限与900秒/2048MiB单段上限不变。
- 前7段真实Windows Job均受限、未超时、75合作退出；第7段769.2455126999994秒按实测收费并标记PAUSED，累计实测5777.9644161999995秒、峰值2004.39453125MiB。没有重开户、重置时间或重复消费。
- 首次暂停接续凭证SHA256为c061c41a2b1327d1e94898e576a6d15f2f6eaf82423d8fa35417065d13f8bf1a。收集程序初次使用六位暂停文件名找不到文件，修正为原三位命名后通过；没有改变原运行文件。

### Notes
- 这只证明首个实际暂停点及原用途接续；第251/390日、压力账户三个暂停点、504日完整独立核账及连续参考对照仍待完成。新源码完整账户仍为2/6，不标记计划completed或取得资格。
- 可使用原公共pause安全暂停；所有已执行日、控制链和消费证据应保留。后续文档提交可revert撤回本说明，不删除原记录、退款或重开FAILED用途。

## 2026-10-08 - Task: 用合成样本定位状态查询的重复开销

### What was done
- 在主目录host_support_v1保存synthetic_state_query_probe.py和SYNTHETIC_STATE_QUERY_PROBE_20261008T042341687166Z.json。仅使用合成字符串和时间戳比较当前查询与局部原型；没有读取行情、启动账户、修改项目源码或冻结SOURCE10。
- 六组Python/Arrow字符串用例覆盖空字符串、中文、缺失值、全缺失和负索引，核对每个标量的类型和值完全一致。原型只用于定位可能的重复解码及时间戳解析开销，不接入正在运行的任务。

### Testing
- 120000次合成文本查询：原方法wall 0.2896502秒、CPU 0.28125秒；字典原型wall 0.0323764秒、CPU 0.03125秒，校验和相同。13536次合成时间戳查询：原方法wall 0.0750933秒、CPU 0.078125秒；4096项有界缓存wall 0.005364秒，命中12972、未命中564。
- 以上仅是两个局部操作的测量；尚未证明真实全池内存、整体加速或账户验收。源码universe_account_inputs_v1.py SHA256仍为8cec7ce4ef4b1412517e38f1b65c412b4247c1bd1136d80ac39e46b872d54d0c。

### Notes
- 当前同一SOURCE10真实504日任务继续按原时限和预算执行。若后续实际证据表明确需修复性能，仍须保留身份、来源、资格和独立核账检查，重新冻结源码并重新验收；不能用本探针声称整份计划完成。
- 可移除本次合成探针撤回诊断方式；正式运行证据、结果与已消费预算保持。后续记录提交可revert撤回本说明。

## 2026-10-08 - Task: 修复长期资格核对的重复标量读取并保留原暂停

### What was done
- SOURCE10真实504日正常账户第10段实测834.8888386秒，仅从第102日推进至第116日；下一段大量时间仍用于资料还原与资格核对。通过原公共pause在控制链追加第3项；原宿主已退出，公共只读status确认账户PAUSED，保留116日检查点、全部资源和原消费。
- 修改universe_account_inputs_v1.py：行情和状态的每个字段只取一次；普通时间文本解析使用最多4096项缓存。每次仍重新判断来源、生命周期、状态、决策时间与可见时间，全部资格和物理/逻辑身份检查保持。未改变资金、股票范围、买卖规则、成本、资源规格或并发。
- test_universe_account_inputs_v1.py新增单次解码、可见时间前后判断、缓存上界与非法日期反例；LONG_HORIZON_UNIVERSE_RESEARCH_GUIDE.md说明这种有限复用。主目录host_support_v1保留反例/最终XML、合成对照、原公共暂停状态、绑定摘要和新源码冻结辅助程序。

### Testing
- 改前三个回归用例实际失败：字段重复读取、同一时间重复解析。修复后输入与身份129项通过；增加缓存边界用例后，最终输入、身份、资格、公共投影、流式执行、独立核验六组202项通过，186.47秒，无失败或跳过；git diff --check通过。
- 36只合成证券、564个合成session的完整输入核对：原方法10.1259788秒wall/10.03125秒CPU，修复5.6822753秒wall/5.546875秒CPU；输入身份、资格结论及夹具身份完全一致。这不是实际市场日历或真实全池性能验收，不外推整体耗时。
- SOURCE10暂停凭证SHA256 dbb88bdcb24f181180a53a10a5c2c2d940cb2cd5379c340997f759dd2db30c90；116日、11个实测段9137.010524999998秒、峰值2013.1796875MiB，全部真实Windows Job受限，原账户预算仍CONSUMED且used=limit=1。第11段合作时间到界退出，宿主按原PAUSE控制暂停；验收宿主因剩余规定中断点未完成退出1，并非六账户通过。
- 修复绑定摘要SHA256 03dbbcabf6dece78129f79a8547125f1faba75a5f21eda505e331b0bb96846f1。冻结SOURCE10全部540原件未变，原252日完整通过凭证未变；新源码必须重新完成六账户。

### Notes
- 原暂停收集辅助程序曾用逐项浮点加和与宿主累计值做逐位比较，出现2e-12秒运算次序差异；未写正式凭证前改为核对每段实际收费等于实测，并以最后派发charged_before加原收费精确核对宿主。没有修改真实回执或放宽账户金额核验。
- 新冻结辅助程序只接受本模块及其测试相对SOURCE10变化、已提交的干净源码、上述202项和原暂停/消费凭证；固定同一资料清单、规则、日期、5万元、252/504、BASE/STRESS和原绝对到期，不退款、不重开旧失败，也不把旧252结果当作新源码通过。当前整份计划仍未完成。
- 可revert本次性能修复退回1928c39，暂停新源码用途并保留原资料、检查点、消费和失败；不允许旧源码继续写入来源身份不同的新任务。文档可随相应提交revert，原证据不删除。

## 2026-10-08 - Task: 冻结SOURCE11并启动同范围六账户验收

### What was done
- 性能修复提交c720d134b16015dad271c1d5f82f1962893671c4已推送到原codex分支。SOURCE11在持久E盘复制原540份运行来源，相对SOURCE10只有universe_account_inputs_v1.py变化；单元测试不在运行来源清单中，另由修复摘要和Git提交绑定。
- SOURCE11清单SHA256为34774f0bb8b1aad6e61db1b661fd585e09b0b400d5ddf55337029efbb0554a42；u8_v10按原公共入口于04:58:36 UTC启动六账户验收。资料清单694d12b4、固定规则c7634f41、4607登记股票、252/504窗口、5万元、BASE/STRESS、资源规格和2026-10-10绝对到期均与上一轮逐项相同。
- 新ENGINEERING_ATTEMPT_HISTORY继承旧原件引用并追加SOURCE10两项252完成、504暂停116日和三项真实账户消费；原900秒未知段保守收费保留，旧失败和消费不改写。宿主、配置、监控、冻结和执行辅助程序均保存在E盘，不依赖此前被清理的C盘临时文件。

### Testing
- 冻结前540份原字节、当前提交、202项最终XML、修复与暂停凭证、旧任务状态和原预算核对通过。首次只读冻结检查误把测试文件也计入540份运行来源，拒绝且未创建新来源或作业；按真实清单核对运行模块、另查测试哈希后通过，未扩大允许的代码变化。
- SOURCE11主目录安全同步辅助程序绑定新清单和c720d13，9项隔离Git用例实际通过，SAFE_SYNC_SOURCE11_CHECK_20261008T050617434757Z.json绑定helper SHA256 50238482c58110cda9445e9b31b1a2424f4973f06497630f933e4f746bdebcb7。第一轮一个用例仍期待SOURCE10状态名称而失败，纠正为SOURCE11固定名称后通过；原失败回执保留，未修改同步逻辑或外来文件。
- 主目录只读准备实际通过，540份来源匹配，外来progress.md原字节哈希仍6417f8a0，113662个外来未跟踪项保留，main_mutated=false。最终服务器合并SHA、实际同步仍待六账户及最终CI通过。
- c720d13远端十个PR工作流均保留；只对同HEAD且有对应PR的4个重复push申请取消，回执CI_DUPLICATE_PUSH_CANCELLATIONS_c720d13.json保留，取消不算通过。当前新源码仅进入真实准备阶段，不以SOURCE10的252结果代替。

### Notes
- 当前真实六账户、规定的实际暂停点与连续对照、发布凭证、最终交付HEAD的CI、main合并同步和开发工作区清理均继续待完成；整份计划仍active，正式策略/Paper资格保持false。
- 可用原公共pause安全暂停新任务，保留所有段、未确认耗时、结果和消费；回滚c720d13时停止向新来源用途写入，旧失败不重开，绝对授权不延长。最后清理仅限本次开发工作区，外来研究资料与原验收证据保留。

## 2026-10-08 - Task: 按真实采样修正冻结资料解码与事件日期重复解析

### What was done
- 使用外部只读py-spy采样原SOURCE11真实reader：续跑首段983个有效样本中725个位于Arrow列to_pylist；随后876个样本显示事件生效日被逐报价重新解析。采样不注入代码、不读取局部变量、不重设原资源时钟；部分非阻塞采样失败数量和原始日志保留在host_support_v1。
- universe_data_provider_v1.py仅对原对象文本列按8192行批次解码唯一文本，再按整数索引恢复object/None和跨批共享对象；其他类型、SHA/封存范围检查、来源资格与身份算法保留。universe_account_inputs_v1.py将本次价格衔接核对的事件日期解析移出逐报价循环，无效日期仍使原后继报价产生缺口。
- 新增空文本、Unicode、缺值、跨批对象共享、冻结身份、停牌期间除息及错误事件日期回归；同步更新docs/LONG_HORIZON_UNIVERSE_RESEARCH_GUIDE.md。只在隔离开发工作区改代码，SOURCE11的540份原件逐字节未改。
- 原公共pause回执da83b00748eb886b732a252ddede6856d063e97acd0e755a60eb94d92784e9af于05:36:12 UTC登记。原宿主05:38:15 UTC退出1，账户安全停在20231218第1个完整日；两段实际1465.7106607秒、峰值2005.8203125 MiB，预算仍一次CONSUMED，未退款、未成功结算、未启动STRESS。SOURCE11_PERFORMANCE_PAUSE_20261008.json绑定原公共状态和消费，SHA256为7ba64e4bdee51b31607b3d8004a872d448f7eacf20404d960228a48896e6b2db。

### Testing
- LOAD_HOTSPOT_CORRECTED_RED_20261008.xml：修复前同一有效事件日期随每个后继报价重复解析，1失败/3通过。第一份红灯夹具缺少真实条款字段，随后仅修正夹具并独立保留两份XML。第一次修复回归发现空large_string批次的Arrow字典转换不受支持，保留失败XML并让零行保持原空值路径。
- LOAD_HOTSPOT_CORRECTED_GREEN_20261008.xml：198通过、1跳过；跳过为Windows账户无法创建符号链接的既有用例。LOAD_HOTSPOT_INTEGRATION_20261008.xml：129通过、0失败、0跳过，覆盖身份、资格、提交、流式账户、独占输入和独立核账。git diff --check通过。
- 同一真实冻结父输入253万行状态、252万行日线/辅助表串行开发诊断，两版完整input_identity、全部dtype、原件SHA、267项价格缺口及其哈希完全一致。状态读取44.8954→4.4694秒，价格衔接37.4410→10.4221秒；有界子进程总96.4328→23.4584秒，峰值1175.0195→1267.4297 MiB，均900秒/2048 MiB、Windows Job、单线程，资源原件在LOAD_HOTSPOT_REAL_DATA_COMPARISON_20261008。
- 诊断首次在派发前误把CIM查询命令自身识别为reader，未启动任何受限计算；修正为仅检查python.exe并保留PRE_DISPATCH_GUARD_CORRECTION.json，不重开失败计算或改预算。

### Notes
- 上述为真实文件读取诊断和自动测试，不能代替六个账户完成。完整公共加载器的2 GiB内存验证继续执行；新源码须新冻结并按原规则、股票、资金、日期、成本、暂停点及期限重跑六账户，不能将SOURCE11部分结果算作新源码通过。
- 回滚点为c720d13：revert本次两个性能修复并保留所有原件、失败、暂停与消费。独立诊断无账户交易或策略资格；旧工程历史累计2700秒未知耗时保守收费仍保留。
- 完整原公共加载器随后实际通过：同一冻结输入a8f84cd5与范围16db3115，4607登记/3669合格/938排除，412.6357521秒、峰值1999.5625 MiB、Windows Job、退出0；没有执行账户。LOAD_HOTSPOT_FIX_SUMMARY_20261008.json（SHA256 7a32db9ff5954305058c7fd47902e1a5a9052c6deda808e2466663fd19bc2b39）绑定327项通过、1项明确跳过、原始红灯、真实诊断与SOURCE11暂停。相同小摘要保存在reports/long_horizon_universe_acceptance_v1/load_performance_review/，完整原件留主目录。


## 2026-10-08 - Task: 消除全池准备重复复制并验证内存边界

### What was done

- 保留 SOURCE12 原始全池 PREPARE 失败：265.440809300002 秒、Windows Job 峰值 2057.265625 MiB、退出码 3221225477；尚无账户 START。失败资源与真实准备收费、原始回执及 540 文件冻结来源逐项核对，未重开或退款。
- `src/chanlun_trader/research_factory/universe_data_provider_v1.py` 对临时文本字典使用局部系统内存池；对本次读入的新表执行完整内部认证，消除整套数据的额外复制。公开 inputs 构造器仍复制外部输入，未开放跳过检查的参数。
- `tests/research_factory/test_universe_frozen_io_v1.py` 与 `test_tdx_research_adapter_v1.py` 增加默认内存池不变、逐批空值/共享文本、内部新表复用以及外部调用方隔离的验证；`docs/LONG_HORIZON_UNIVERSE_RESEARCH_GUIDE.md` 说明完整准备与单独加载验收的区别。
- 原失败证明及新实测摘要保存于 `reports/long_horizon_universe_acceptance_v1/load_performance_review/`，完整开发诊断原件位于主目录的同名验收根目录 `host_support_v1/`。

### Testing

- 复制回归先失败后通过：`OWNED_PROVIDER_PREPARATION_RED_20261008.xml` 保留 1 项实际失败；最终 `OWNED_PROVIDER_PREPARATION_GREEN_20261008.xml` 为 329 passed、1 skipped（Windows 当前账户不能创建符号链接的既有测试）。涵盖原件边界、输入身份、全池资格、公共提交、流式执行和独立证据检查。
- 仅调整临时字典内存池的完整准备通过，但峰值 2047.4609375 MiB、267.5812107999991 秒，余量太小；原件及结果保留，没有当成六账户验收。
- 再消除内部新表重复复制后，全部 4607 只登记股票的真实完整提供器准备及冻结通过：248.876637600 秒、峰值 1949.847656250 MiB，900 秒 / 2048 MiB / 单线程 Windows Job 边界不变。与 SOURCE11 原始父资料的窗口、资格、数据身份及 daily/turn/states 文件字节分别一致。
- 人工风险审查覆盖来源 SHA、整文件范围授权、阶段与就绪检查、外部输入隔离、默认内存池不变及旧预算保留；诊断没有执行账户、申请正式资格或读取封存窗口。

### Notes

- 六账户真实 U8 验收仍未完成，本条记录只证明新源码的全池准备及回归检查。下一轮需重新冻结新源码，独立计入所有六账户用途及准备、核验、报告成本；不继承旧结果为新源码通过。
- 本次原失败准备收费全部保留；既有账户消费、2700 秒历史保守收费和旧暂停记录不变。研究策略、资金、股票范围、日期、成本、资源上限与原绝对到期时间不变。
- 回滚点为 `1ab9cae1b6c1fede8e23d911e994ddca2b9de3fb`；可反向回滚本次内存修改并停止新派发，保留原作业、预算及失败证明。该点含已记录的全池准备超限，不能据此声称当前长期能力发布通过。


## 2026-10-08 - Task: 修正扫描认证复用测试观察点并继续冻结六账户验收

### What was done

- SOURCE13 在 `6463ed1089afc0afba1675aaf02e0b0abdf37ffc` 重新冻结，540 文件清单 SHA 为 `4f88860048d8bd6cfe45fd62b83f09f5b7440af6cf583b3612d296a33bc26219`。同策略、资金、股票、成本、252/504 窗口、资源与原绝对到期时间重新登记六用途，所有旧失败、14 个已消费账户用途及保守收费保留。
- 原公共宿主于 2026-10-08T06:35:15 UTC 启动 `u8_v12`；252 的正式准备两段完成，4607 登记股票、3669 合格、938 排除，累计 680.5575793000025 秒、峰值 1974.4140625 MiB。第一账户 START 一次按原生预算消费，仍在运行，不称六账户完成。
- 远端 Ubuntu 全池套件暴露 3 项计数失败（996 passed、2 skipped）：测试仅观察 `__init__`，内部新表完整认证使用 `_initialize` 因而未被统计。`tests/research_factory/test_universe_scan_service_v1.py` 改为观察实际完整认证，保留一次工作进程、一次认证、字段/预热/身份/覆盖/信号/UNKNOWN/冻结形状的原断言，并增加不重复复制断言；未改运行源码。
- 主目录同步工具只重绑 SOURCE13 身份，9 项独立 Git 夹具通过，真实主目录只读预检也通过（113662 外来文件，外来 progress SHA `6417f8a018eae0e37169c4cc8a5d6b9a0d65f101a0a308d85945f50fc8ec9b36`），尚未同步。

### Testing

- 本机精确复现原 3 项失败，`SCAN_INITIALIZER_OBSERVER_RED_20261008.xml` 完整保留；观察点修正后整个扫描服务模块 48 passed，原业务断言全部保留且新增内存复用断言，没有跳过或放宽失败用例。
- 测试变更前后 WORK 与原 SOURCE13 540 个运行文件 SHA 逐项一致；真实账户继续使用原冻结包，不热补丁、不重新登记预算。
- 风险审查及红绿/远端原始失败绑定见 `load_performance_review/SCAN_INITIALIZER_OBSERVER_REVIEW_20261008.json`。SOURCE13 的 10 个 PR 工作流保留；重复 push 取消辅助程序的失败与服务器实际状态单独保留，取消不计通过，未重复发取消请求。

### Notes

- U8 的独立核账、多次真实恢复、连续参考对照、双报告和六用途完整完成证明仍待实际运行，计划仍 active。当前任务不授予策略、统计或 Paper 资格，未读取封存窗口。
- 本次仅测试观察方式与追加证据；可反向回滚相应提交或恢复该测试至 `6463ed1`，所有已冻结源码、原作业及消费记录保持原样。


## 2026-10-08 - Task: 修复正式账户装载合并峰值并核验真实入口开销

### What was done

- 补充上一条运行快照后的真实结果：SOURCE13 宿主于 06:54:38 UTC 退出 1。完整准备通过，但第一账户恢复正式子资料时，原整表 concat 触发内存超限；411.20247219999874 秒、2058.96484375 MiB，0 个账户日、1 次 START 与一次 CONSUMED。原结算 FAILED、收费与 540 文件冻结原件保留，未重开或退款。
- `src/chanlun_trader/research_factory/universe_data_provider_v1.py` 只改冻结批次的最终组装：逐列拼接并释放该列的批次副本，再以不重复复制方式构造完整表；仅索引表保留原合并路径。完整父资料、合格范围、正式子资料、身份及就绪核验未删减。
- `tests/research_factory/test_universe_frozen_io_v1.py` 增加跨三批的对象文本/中文/None、可空整数与布尔、分类、时区、float32、列顺序名称、RangeIndex、重复命名索引、MultiIndex 和仅索引表逐值/逐类型验证；更新长期使用说明。
- 真实加载诊断先导入 `scripts.run_strategy_account_v1`、构造同一公共策略及后端并执行原 `prepare`，将生产入口开销纳入原 900 秒 / 2048 MiB / 单线程 Windows Job 实测；没有执行账户或复用原失败账户预算。

### Testing

- `COLUMNWISE_FROZEN_ASSEMBLY_GREEN_20261008.xml`：381 passed、1 skipped，跳过为既有 Windows 无符号链接权限用例。覆盖冻结 IO、账户输入、TDX 接入、全池资格与扫描、提交、内部复用、流式执行和独立核账。
- 包含实际账户入口的完整公共加载诊断通过：435.422189000 秒、1967.093750000 MiB，同一输入与范围身份，4607 登记 / 3669 合格 / 938 排除。该检查只证明装载，没有开始交易日。
- 完整提供器准备及冻结诊断通过：244.066451100 秒、1893.089843750 MiB。全部 4607 登记股票的窗口、资格、身份及 daily/turn/states 文件原字节与 SOURCE13 原生 PARENT 相同，默认 Arrow 内存池保持原样。
- 人工风险检查保留对象空值、各类索引、类型/顺序、原 SHA 与全范围核验、外部可变输入隔离和预算边界；小摘要与失败证明在 `load_performance_review/`，完整诊断原件仍在主目录 `host_support_v1/`。

### Notes

- 这两项是有界开发诊断，不是六账户 U8 通过证明。当前整份计划仍未完成；后续按同一规则、资金、股票、252/504 日期、成本、资源及原 2026-10-10 绝对期限新冻结、重新登记并执行全部六用途。
- 原 SOURCE13 的准备 680.5575793000025 秒与账户 411.20247219999874 秒全部保留；历史已消费用途和 2700 秒保守未知收费不退款、不改写，正式策略与 Paper 资格仍 false。
- 回滚点 `5d849c483945c5525b8312e1564426ef6bf1303c`；可反向回滚本次逐列合并与文档，停止向新来源派发，保留所有原失败、原件和消费。旧点含已知装载超限，不能据此声明长期能力发布完成。


## 2026-10-08 - Task: 冻结SOURCE14并完成正式准备与首次原生换段

### What was done

- 内存修复已提交并推送 `652ecede5f6967c828f2aff30038c921951b90a8`。SOURCE14 540 文件清单 SHA 为 `dddfe23467b2a85fc33a5ca82ff2e0e975a7e2c0536e2b7ee5d46ff19065df82`；相对 SOURCE13 仅提供器最终组装变化，测试独立绑定到 381 passed / 1 skipped 的修复凭证。
- `u8_v13` 按原公共六账户宿主于 07:28:45 UTC 启动。配置 SHA `50d344ff`、资料 SHA `694d12b4`、固定规则 `c7634f41`、4607 股票、5万元、252/504、BASE/STRESS、原资源及 2026-10-10 绝对到期不变。
- 原生准备两段通过，4607 登记 / 3669 合格 / 938 排除；第一 252 BASE START 后完成全池指标，并沿原账户正常换段，未重置资金或再次消费账户用途。
- 新 `ENGINEERING_ATTEMPT_HISTORY.json` 继承原件并追加 SOURCE13 0日失败、1次消费、准备和账户收费；此前15个账户用途与历史2700秒保守未知收费保留。新两项只读开发诊断单独记录679.4886401000003秒，不计为账户通过或退款。

### Testing

- 正式准备累计 671.512026400 秒、峰值 1940.644531250 MiB，原 Windows Job / 2048 MiB 边界，返回75正常换段及0完成；正式范围回执已生成。
- 首个账户段 832.789578100 秒、峰值 1973.203125000 MiB、返回75，资源与真实收费逐项一致，原生 CONTINUE 后第二段已派发。07:55:53 UTC只读快照为已提交2日，末日20231219；仍未称252账户完成。
- 安全同步辅助程序只重绑新来源，9项独立Git用例通过，helper SHA `1b93e558103725be52da547597124035bc671c0600577e29a14425eb296fad04`。真实主目录只读准备通过：113662 外来未跟踪项、外来 progress SHA `6417f8a018eae0e37169c4cc8a5d6b9a0d65f101a0a308d85945f50fc8ec9b36`，main_mutated=false。
- 此记录时 WORK 与 SOURCE14 540 文件原字节逐项匹配；完整证据在主目录 host_support_v1，小进度摘要保存在 load_performance_review。最终Linux/Windows矩阵仍运行，已通过部分作业不替代全部CI完成。

### Notes

- 全部六账户、独立逐日核账、六次真实暂停恢复、504连续参考比较、双报告和正式发布证明继续待完成；整份计划仍active，正式策略、统计及Paper资格均未授予。
- 最终服务器合并、main同步和本次开发工作区归档须在完整验收与最终CI后进行；其他AI研究文件保持原样。
- 可用原公共pause安全停止到已提交边界并保留所有消费和证据；回滚点为652eced的上一提交5d849c4，新源码任务不得写入旧来源用途，旧失败不重开、到期不延长。


## 2026-10-08 - Task: 完成当前运行源码的Linux与Windows CI并核对既有安全扫描

### What was done

- 精确提交 `652ecede5f6967c828f2aff30038c921951b90a8` 的10个PR测试工作流全部成功，25个实际作业全部成功，包含Ubuntu/Windows的工作流、正式范围、全池与长期回测8项矩阵，以及规则集要求的 `Deterministic governance suite`。
- 只计算对应HEAD、event=pull_request的原生完成作业；重复push、取消或此前HEAD均未计为通过。小原始元数据摘要保存于 `reports/long_horizon_universe_acceptance_v1/security_review/CI_COMPLETION_652eced.json`，SHA `938bb91f4be2fd1fba191ab0d561d8535c01b7f0aacd0d72fb14e41cfe571aa6`。
- 通过Sonar官方PR清单、issues、quality-gate和GitHub当前HEAD check再次只读核对：仍为既有11条OPEN告警，键/规则/状态/位置一致，4份已人工复核的相关源码原字节与1928c39一致，没有新增告警；服务端安全门禁仍ERROR，不称安全扫描已通过或告警已关闭。

### Testing

- 当前源码的8项双平台矩阵最终结论为success；全部10个PR工作流、25个作业的HEAD和完成状态由官方Actions API再次核对。
- Sonar复核摘要SHA `04b3cfc6b144fe9fb28dba469acbcfa3d53be6bf3fa1275b59c799ecca217411`；官方原件及当前GitHub扫描check保存在主目录host_support_v1。没有修改服务端告警、压制规则、增加扫描排除或降低门禁。
- 本条仅追加元数据和进度，没有修改SOURCE14运行源码或启动额外账户。最新09:06:08 UTC只读快照为252 BASE完成191日、第7段已派发；其他5用途与独立核账仍待完成。

### Notes

- CI成功不是六账户真实U8完成证明，整份计划仍active，当前未合并或同步main。正式策略、统计与Paper资格仍false。
- 最终发布元数据产生后，最终交付HEAD仍须重新核对规定CI与当前安全扫描；不以本次中间HEAD替代最终HEAD检查。
- 回滚点为652eced；本条文档和小摘要可随最终文档提交反向回滚，原生CI/Sonar证据、已冻结作业与所有预算消费保留不删除。


## 2026-10-08 - Task: 保留系统重启证据并沿SOURCE14原公共任务恢复

### What was done

- Windows实际最后启动时间为2026-10-08 22:32:30.5 +08:00；原宿主与第10段工作进程消失，原HOST_START保留、没有伪造HOST_EXIT或成功结算。
- 540份冻结运行源码、原提交652eced、配置、输入与未延长的绝对授权逐项复核通过后，再运行原六账户harness。既有任务选择公共resume，不创建新账户或重开失败终态。
- 将恢复前账户252日、独立核账252日（complete=false）、原START/JOB/派发原件复制到不可覆盖的REBOOT_RECOVERY_001_ORIGINALS；公共恢复已经推进预算资源账，如实保留恢复前预算哈希引用及恢复后原件，未重建旧预算；小摘要SOURCE14_OS_REBOOT_RECOVERY_20261008.json SHA 4ac73b0848840a641eaab49fcf961bc9eb0021981aa76ce82d7a41b0bb0d8a5b保存于load_performance_review。

### Testing

- 实际公共恢复把第10段缺失耗时回执按派发上界900秒计入UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND，原已测7529.9408667秒保持，累计8429.9408667秒；第11段绑定同一receipt/profile及前段计费链。
- 原BASE START哈希未变、原预算reservation_counter=2且两次CONSUMED；STRESS未START、旧失败未重开、未退款、未提高14400/900秒及2048 MiB上限。独立只读复核确认账户和审计均跳过已经提交的252日期，继续最后核验尾部。
- 仅添加主目录恢复宿主辅助脚本、恢复证据及本条开发区进度；SOURCE14运行源码原字节保持，最终六账户与504暂停/连续参考仍待验收。

### Notes

- 这次额外真实系统重启不替代事前冻结的504日80/251/390三次暂停及连续参考；当前未合并main、未授予策略资格。
- 历史未知计费2700秒另保留，本次当前用途再计900秒，最终资源发布必须分别展示且不可抵消。
- 可通过原公共pause安全暂停到提交边界；回滚点仍652eced，恢复辅助脚本可移除而原授权、START、恢复控制和所有消费原件保留。


## 2026-10-08 - Task: 完成SOURCE14第一份真实252日账户及系统重启恢复

### What was done

- 原SOURCE14 252日BASE已HISTORICAL_MODELED_ACCOUNT_COMPLETED，独立reconciliation.passed=true、审计complete=true，共252日；原START哈希不变，结算completed=true与实际结果SHA一致。
- 用途实际计费8900.8323161秒，其中未知段保守900秒、10份实际资源回执；11段派发均属于原同一用途。峰值1983.18359375 MiB，原14400/900秒和2048 MiB上限不变。
- 小摘要REAL_SOURCE14_FIRST_252_ACCOUNT_PASS_20261008.json SHA 4203007a953afc852d3f6c6fb8890c45cb7cc736bbe2b5410c1638ec6b7d43c5保存在load_performance_review，完整运行及重启原件保存在主目录。系统已继续原STRESS用途，不重开此前失败、不继承旧来源结果。

### Testing

- 核对原JOB/START/结果/审计/结算、全部11份计费及10份实际资源回执；原机重启造成的第10段按900秒保守计费。账务独立重建252日与结果核验通过，结算结果哈希一致。
- 本条仅记录已完成的第一账户，不把它写成两成本公共核验/报告或六账户通过；504暂停、连续参考与发布元数据仍待完成。

### Notes

- 策略资格false，独立验证与Paper均NOT_RUN；当前没有合并main、同步主目录或清理正在使用的开发区。
- 回滚点652eced；可安全暂停原公共任务，既有消费、失败历史、实际资源和本次重启证据保持不可删除。


## 2026-10-09 - Task: 完成SOURCE14原生一年期公共回测、独立核验和双报告

### What was done

- SOURCE14原252日BASE/STRESS均完成，公共VERIFICATION.advance_allowed=true、两成员status=PASS；原生EXECUTION确认公共全流程完成，信号/实际账户双报告已生成。
- 同一全池检查4607只、合格3669只、排除938只，同一冻结输入a8f84cd5；每账户独立重建252日，结果与结算SHA一致。原资格、成本、资金50000及资源上限未变。
- REAL_SOURCE14_252_PIPELINE_PASS_20261009.json SHA 32d8ae880cb2aca471b2ce1a121fc786b94ca163e924d4992570a7377b80c404保存在load_performance_review，绑定各成员START/RESULT/AUDIT/SETTLEMENT、公共核验、报告和计算资源原件。504准备已由原harness继续。

### Testing

- 两账户各252日reconciliation.passed=true，独立审计complete=true；双报告sessions=252、initial_cash=50000，strategy_qualified/formal_method_applicable/paper_qualified均false。
- BASE实测8000.8323161秒加原重启未知收费900秒，STRESS实测7197.7635144秒；全部实际段在2048 MiB以内。公共核验851.1583409秒、报告1536.54671秒已原生完成。
- 仅确认一年期公共整链路，不声明504/连续参考/六次暂停、六账户或最终发布通过；既有首次账户和恢复历史摘要原样保留。

### Notes

- HISTORICAL_MODELED，策略无资格，独立验证和Paper均NOT_RUN；尚未合并main或删除开发区。
- 本次只新增交付证据和进度，冻结运行源码无改动。回滚点652eced；可通过公共入口安全暂停原任务，保留消费和收费链。


## 2026-10-09 - Task: 保留第二次实际系统重启原件并恢复SOURCE14同一504日任务

### What was done

- Windows于2026-10-09 08:18:12.5 +08:00实际重启，原HOST_RESUME_001、账户第20段与reader进程全部消失；未伪造HOST_EXIT、费用实测或成功结算。
- 原540份SOURCE14源码、652eced、配置、绝对到期2026-10-10 08:00 +08:00、原504输入/JOB/START/预算及19段资源复核有效；引擎已提交504日、独立核账417日complete=false。
- 恢复前12份原件（包括旧预算原字节、504检查点/审计、三份暂停证据及一年期完成记录）先按SHA保存在REBOOT_RECOVERY_002_ORIGINALS，再运行同一原harness --execute，自动选PUBLIC_005公共resume。
- 主目录仅新增恢复宿主/证据辅助脚本，更新只读监控的第二宿主退出观察；开发区新增SOURCE14_SECOND_OS_REBOOT_RECOVERY_20261009.json SHA 5c0c49c121022cbdf1e022f68d235a5fef31843d3f288aefabe3ea2e0c3b3f0c及本条进度，冻结运行源码无改动。

### Testing

- 实际第20段按900秒未知保守扣费CONTINUE，累计16401.4384837秒，第21段绑定同一BASE receipt/profile及前段收费链；原28800/900秒、2048 MiB上限不变。
- 原504 BASE START SHA保持，reservation_counter=2且两次CONSUMED；不新开用途、不退款。原三份80/251/390暂停及六份PAUSE/RESUME链身份有效，BASE剩余暂停请求为空，STRESS仍保留三个冻结暂停点。
- 已提交的504引擎日不重做，恢复独立核账第418日起；一年期原EXECUTION原样复用。实际六账户最终证据尚未生成，不把恢复启动写成通过。

### Notes

- 当前SOURCE14两次重启分别产生252 BASE和504 BASE各900秒未知收费，共1800；更早失败/暂停历史2700秒独立保留，不抵消。
- 原预算与恢复前进度原件已先保留，区别于首次重启未能留旧预算原字节的事实；旧摘要不改写。策略无资格，未合并main或清理开发区。
- 回滚点652eced；可用原公共pause安全停在提交边界，旧授权、START、控制链、实际消费和重启证据全部保留。


## 2026-10-09 - Task: 完成SOURCE14第一份真实504日账户及独立核账

### What was done

- 原SOURCE14 504日BASE结算completed=true、HISTORICAL_MODELED_ACCOUNT_COMPLETED；引擎504日、独立审计504日complete=true、reconciliation.passed=true。
- 原START哈希不变；21段派发、20份实际资源共16250.663426秒，加第二次系统重启第20未知段900秒，实际计费17150.663426秒。峰值1979.9296875 MiB，原28800/900秒、2048 MiB上限未变。
- REAL_SOURCE14_FIRST_504_ACCOUNT_PASS_20261009.json SHA 2b404aa3d488082e55c3d6ce2e03ce6d1510011a4789f2909f91128ca3835bf5保存在load_performance_review，绑定原START/RESULT/AUDIT/SETTLEMENT/CHECKPOINT、3次真实暂停及第二次重启恢复。原harness已继续504 STRESS。

### Testing

- 实际结果、独立核账、结算SHA一致，全部504日通过；原21份收费链、20份实测资源核对通过，三份80/251/390 BASE暂停证明保留。
- 当前完成三个账户（252两成本和504正常成本），没有将公共504核验/报告、压力恢复、连续参考及六账户写成通过。冻结运行源码无修改。

### Notes

- HISTORICAL_MODELED，策略资格false，独立验证和Paper均NOT_RUN；尚未最终发布、合并main、同步或删除开发区。
- 回滚点652eced；可用原公共pause安全暂停原任务，保留实际收费、旧失败与恢复历史。


## 2026-10-09 - Task: 完成SOURCE14四个真实分段账户及六次计划内暂停

### What was done

- 252/504日、BASE/STRESS四用途均原生HISTORICAL_MODELED_ACCOUNT_COMPLETED，独立审计252/252/504/504日全部complete=true、reconciliation.passed=true，原结算结果SHA一致。
- 504 STRESS实际24段资源19782.5895158秒、峰值1980.84375 MiB、未知0，原28800/900秒、2048 MiB上限不变；两种成本各80/251/390日六次实际暂停回执齐全。
- REAL_SOURCE14_FOUR_SEGMENTED_ACCOUNTS_PASS_20261009.json SHA 35a2baca7468697763ec47cc2f5b5a5b3540f62cf0f25728fd82824aec913db6保存在load_performance_review，绑定四账户START/RESULT/AUDIT/SETTLEMENT及六次暂停原件。原harness继续504公共核验、报告与连续参考。

### Testing

- 四成员逐一核对引擎日数、独立核账日数、成功结算、结果SHA、实际/未知收费和资源上限；六次暂停用途与事前80/251/390阈值顺序一致。
- 当前仅四个分段账户通过；504公共核验/报告、两份连续参考及18状态/逐日/经济内容对照仍待完成，不声明REAL_ACCEPTANCE存在。

### Notes

- HISTORICAL_MODELED，策略无资格、独立验证/Paper NOT_RUN；尚未最终发布、合并main、同步主目录或删除开发区。
- 回滚点652eced；可用公共pause安全暂停原任务，所有旧失败、1800秒当前未知收费及更早2700秒历史未知收费分别保留，不退款。


## 2026-10-09 - Task: 完成SOURCE14两年期公共核验与双报告并进入连续对照

### What was done

- 原504公共EXECUTION.advance_allowed=true，BASE/STRESS公共核验均PASS，双报告完成；EXECUTION原件SHA 8ba805b72e969cd641655d20e1a058f96cd193e0c787bffecca5feda82df64f4。
- 公共核验实测1047.0966388秒、两段、峰值1981.66796875 MiB；报告4554.9356158秒、六段、峰值2026.8984375 MiB，全部在原定上限内，没有删股、缩短评价区间或换策略。
- 原harness自动建立既有批准的continuous_reference，正常成本参考已START、压力成本待START；仍同一SOURCE14冻结来源。

### Testing

- 原生四个252/504分段账户及各自公共核验/报告完成；六份真实80/251/390暂停证明保留。独立核账252/252/504/504日全部PASS。
- 当前仅进入连续对照，不声明两份连续参考、18项状态/每日/经济内容相等或REAL_ACCEPTANCE完成。未运行新的策略搜索。

### Notes

- 运行源码仍652eced的540份冻结字节；HISTORICAL_MODELED，策略无资格，独立验证/Paper NOT_RUN。尚未合并main、同步主目录或删除开发区。
- 回滚点652eced；可用原公共pause安全暂停分段任务，原始收费、失败与恢复链全部保留。


## 2026-10-09 - Task: 完成SOURCE14正常成本连续参考账户

### What was done

- 原continuous_reference正常成本用途一次进程完成504日，7205.3154765秒、峰值1951.0078125 MiB、returncode=0；RESULT原件SHA 4de9094ed4cfa47bdb4baf454be57c2559823db289e9fd8cd41283d4faf3a401，成功结算绑定原结果。原harness继续压力成本连续参考。
- 只读监控辅助脚本现在对连续用途读取已结算RESULT.reconciliation日数和passed；连续用途不写分段AUDIT.json，所以旧显示0仅是没有分段审计落盘文件，实际RESULT已内置独立核账504日PASS。未修改冻结运行或核验源码。

### Testing

- 实际RESULT状态、reconciliation.passed=true/days=504、结算SHA及Windows Job实测资源通过，输入与原504分段任务保持同一身份358666e7。
- 当前仅五个账户完成，连续参考公共核验/报告、18状态与每日经济对照仍未完成；不声明最终验收通过。

### Notes

- HISTORICAL_MODELED、策略资格false；最终发布、CI、main合并同步与清理仍待完成。
- 回滚点652eced；原始START/预算/派发和资源不删不退款，监控辅助脚本仅改变进度读取显示。


## 2026-10-09 - Task: 结清已知失败连续参考并另立同源码独立工程对照

### What was done

- 第三次Windows重启后只读核实：原连续STRESS RESOURCE写于17:44:32，早于22:19:46重启；returncode=1073807364、非超时、257.99633569999423秒、峰值1481.2890625 MiB。退出原因未知，不能归因于稍后的重启。
- 先保留16份原件（含原预算字节），再调用原治理end_segment/settle写入FAILED及真实计费；charge ID f52f6d087116c09f8d2e157889ded59ffe58667304040b05911c71f23af45b16。旧预算哈希不变，两个原用途仍CONSUMED，不退款、不重开、不补造Worker状态或宿主退出。
- 在u8_v13_reference_retry1另立两个独立工程参考用途，规则/日期/5万元/4607目标股票/输入/540源码字节/8小时规格/2026-10-10T00:00UTC到期不变。新部署复用原分段任务和扫描，逐字节保留两份已完成EXECUTION；原harness仅重跑新BASE/STRESS连续参考、核验、报告及对照，不重跑四个成功分段账户。
- SOURCE14_INDEPENDENT_REFERENCE_REPLACEMENT_20261009.json SHA 2f067100b982474d2809efc3d236260f91fbf51a574126fa1d4a8bd35f8d3766已保留在load_performance_review；私有执行及监控助手放在主目录host_support_v1，不属于运行源码。

### Testing

- 只读540源码字节/HEAD/原件/预算/已完成EXECUTION哈希预检PASS；两项独立只读审查无阻断问题。执行后复核原预算字节不变、FAILED收费与原RESOURCE一致、复制EXECUTION哈希一致。
- 新原生harness已启动并真实冻结新验收计划；当前仍待两项新连续账户、公共核验/报告及最终18状态/逐日/经济内容对照，不声明整体验收完成。

### Notes

- SOURCE14累计有8个账户用途（原6个含1个失败及1个被替代参考，加新参考2个），最终验收矩阵要求4个原成功分段与2个新成功连续账户；全量尝试历史及消耗必须披露，不能将新增用途伪装为原预算余额。
- HISTORICAL_MODELED、策略无资格、独立验证/Paper NOT_RUN。回滚点652eced；不删除已生成历史或退还消耗；发布、main合并同步、开发区清理仍待完成。


## 2026-10-09 - Task: 核实原连续压力参考退出对应的系统关机事件

### What was done

- 只读查询System/User32事件1074：2026-10-09 17:44:31发生开始菜单发起的关闭电源；原Worker RESOURCE于17:44:32落盘，时间相邻。此前记录“原因未知”是当时证据边界；本条追加系统关机对应证据，不改写旧失败或预算。
- SOURCE14_WINDOWS_SHUTDOWN_CORRELATION_20261009.json保留经过最小字段投影的事件ID、记录号、UTC时间及RESOURCE哈希，不复制账户用户名或完整系统事件正文。

### Testing

- 核对事件ID1074、关机类型、来源程序及RESOURCE写入时间差小于3秒；新工程参考继续由原harness运行。

### Notes

- 原FAILED及已消费状态保持；运行源码未变。回滚点652eced，历史证据保留，无系统电源设置变更。


## 2026-10-09 - Task: 有界预检已完成正常连续账户与两年分段账户一致性

### What was done

- SOURCE14_PRELIMINARY_BASE_COMPARISON_20261009.json SHA e6b67d4a830c8ce25b216821e8681813971bdaf00790887bbbdc5dcb92189cad保存旧已成功连续BASE与原504分段BASE的提前只读对照；不使用或改动新retry1。
- 按原harness实际字段比较6组经济数据、18项账户状态；两份checkpoint自身身份及manifest链验证通过，504日期及payload_identity一致。未读日gzip原件，因此明确只是PRELIMINARY_COMPLETED_BASE_COMPARISON，不能替代最终逐日对照。
- 同步修正文档AUTONOMOUS_RESEARCH_USER_GUIDE.md中的旧说明：V4已支持冻结数值评分排序，V3保留原顺序，ATR止损仍未接通。

### Testing

- Windows Job 512 MiB/120秒受限诊断，单数值线程；实际3.3630798秒、峰值210.46875 MiB、rc0、未超时，4份账户原件及源码前后哈希不变。
- 未启动研究账户、未改预算/运行源码，未伪称最终六账户通过；新连续BASE继续运行。文档仅局部文字修正。

### Notes

- 本诊断单独计为开发验证3.3630798秒，不减免原研究收费。回滚点652eced，保留所有已生成证据；历史盈利及Paper资格不在本项验证范围。


## 2026-10-10 - Task: 纠正连续参考审计文件定位及监控解释

### What was done

- 更正此前“连续参考不写AUDIT/OWN_FEATURE缓存”的解释：冻结reference_config的checkpoint目录比JOB根多一层account，实际审计、指标及独立指标缓存都位于checkpoint_path.parent。旧已成功连续BASE实际有3669份执行特征、3669份独立特征和完整504日AUDIT。原错误记录保留，本条追加更正。
- 仅修改主目录host_support_v1中的monitor_u8_v13.py、monitor_source14_reference_retry1.py，按冻结checkpoint.parent读取实际审计及缓存；不修改运行/核验源码或账户原件。
- 公共VERIFICATION通过research_evidence_v1定位实际AUDIT，再由universe_evidence_v2验证完整审计身份、逐日原件及输入；不会直接相信RESULT.reconciliation自报值。REPORT与分段账户共用原受限汇总路径。

### Testing

- 只读核对原JOB路径与嵌套实际文件；监控修正后正确显示旧连续BASE的504日独立审计，以及新BASE在00:37已完成181/504日独立核账。新BASE引擎504日已完成但尚未结算，不声明最终验收通过。

### Notes

- 回滚点652eced；监控是只读辅助文件，所有原账务、预算、运行源码和历史失败保留。先前SOURCE14单次成功BASE的结果/结算证据本身不受监控解释更正影响。


## 2026-10-10 - Task: 新独立正常成本连续参考完成504日及独立核账

### What was done

- u8_v13_reference_retry1原harness正常成本连续参考单次进程完成504交易日及独立核账，成功结算。实际7178.8695695999995秒，峰值1952.12890625 MiB，rc0、非超时、仅1段；RESULT SHA f56aa15040b3c7725ababeffd96312693878fa487c82282aee43db79db8d187e。
- SOURCE14_INDEPENDENT_REFERENCE_BASE_PASS_20261010.json SHA db5082c486c19c652c0ab3ab8abade42bd83eab5ad9365518939c1eaec4d300c保留新JOB/RESULT/SETTLEMENT/RESOURCE、正确嵌套AUDIT及checkpoint原件引用。压力成本新连续参考继续运行。

### Testing

- 核对真实Windows Job资源、1段、504日AUDIT.complete=true、RESULT独立核账passed、结算结果SHA及同一冻结输入；未借用旧连续BASE顶替新用途。
- 验收矩阵当前5/6完成；参考公共核验、双报告及最终18状态/逐日/经济内容比较仍待完成，不声明REAL_ACCEPTANCE存在。

### Notes

- HISTORICAL_MODELED、策略无资格、独立验证/Paper NOT_RUN。回滚点652eced，所有原失败消费与新参考用途分开保留；尚未合并同步或清理开发区。


## 2026-10-10 - Task: 为交付后清理开发目录保留测试原件

### What was done

- 在主目录 reports/long_horizon_universe_acceptance_v1/worktree_test_archive_source14/ 保留开发区78份XML、日志及标准输出/错误文本，共2,592,057字节；不复制行情、环境或大型夹具。未被正式JSON引用的旧文本明确标记 OLD_TEST_TEXT_NOT_REFERENCED_BY_CURATED_JSON，不冒充本版新增通过证据。
- MANIFEST.json SHA 99a429655652ef1b1a199d372cbd27cefc9c16d381a1245aa08b8d927a30108f 记录原路径、副本及逐项哈希，供后续受管开发目录归档后查询。

### Testing

- 24份正式JSON的开发区文件引用均无缺失；14项已在主目录的XML/log原件存在且声明哈希匹配。78份测试文本源文件和副本独立读取SHA全部相同，复制文本总量低于20 MiB。
- 未修改源码、账户原件、预算、Git索引或主目录用户研究文件，未提前删除或归档开发目录。

### Notes

- 回滚点652eced；本项仅保留测试资料，原开发区仍存在，后续仅在验收、合并和主目录同步完成后归档本次受管工作区。既有失败和消耗记录继续保留。


## 2026-10-10 - Task: 限定旧版900秒说明并核对最终主目录交付条件

### What was done

- 局部修订 docs/AUTONOMOUS_RESEARCH_USER_GUIDE.md、docs/RESEARCH_LIFECYCLE_OPERATIONS.md 的旧全范围扫描/恢复说明，标明旧版本；补充长期V3每段900秒、原用途累计4/8小时和原授权到期的区别，并链接长期指南。
- 独立只读复核U1–U7未发现新增实现阻断。发现主目录 frontend/dist 是忽略文件且仍为旧产物，列入合并同步后从最终源码重新构建、核对实际界面的必要收尾；当前不更改正在验收的冻结源码或启动服务。

### Testing

- git diff --check通过。文档所述版本边界已与执行规格及公共恢复路径核对；未重跑已通过的源码测试，未读取市场行情。
- U8仍为5/6完成，不能由文档核对代替真实验收；发布后需通过 publication --long-horizon读取动态凭证，不能手改生成能力目录的静态状态冒充通过。

### Notes

- 回滚点652eced，恢复本次两份手写文档即可撤销说明修改。前端构建、原生验收、发布、CI、main合并同步及受管开发区归档仍未宣称完成。


## 2026-10-10 - Task: 最后一个独立连续压力账户完成及六账户矩阵齐备

### What was done

- 新独立压力连续参考单次进程完成504交易日与504日独立核账，成功结算。实际7128.1490371秒、峰值1978.14453125 MiB，rc0、非超时、仅1段；RESULT SHA 86aad2352324dccf9ba44e8d3ce8393cf8f259803cc4e2726c58742cabd4ea41。
- SOURCE14_INDEPENDENT_REFERENCE_STRESS_PASS_20261010.json SHA 820b230758388adf5689581e80dd1e7fdf31e70b559dd1f00e57562fd352b2da保留真实JOB/结果/结算/Windows资源/嵌套AUDIT/checkpoint引用，明确六账户完成但最终公共核验与报告尚待完成。
- 原harness自动进入新连续参考的公共VERIFICATION；原252/504四账户与公共核验/报告均继续复用已完成同字节证据，未重复执行或重开额度。

### Testing

- 核对同一输入358666e7、完整独立AUDIT.complete=true且504日、RESULT核账PASS、结算绑定实际结果SHA、单段Windows Job强制资源及2048 MiB内存上限。
- 最终逐日经济与18项状态对照、连续参考公共核验及双报告未完成，REAL_ACCEPTANCE尚不存在；不以6/6账户完成冒充整体验收通过。

### Notes

- 六个成功矩阵账户与SOURCE14累计8个已消费用途分别记载，旧失败及被替代参考原件保留；此前15个用途的历史未删除或退款。
- HISTORICAL_MODELED、策略无资格、独立验证/Paper NOT_RUN。回滚点652eced；发布、最终CI、main合并同步及受管目录归档仍待完成。


## 2026-10-10 - Task: 原生六账户长周期验收完成并定位发布规范化误拒

### What was done

- u8_v13_reference_retry1/actual/REAL_ACCEPTANCE.json原生生成，SHA 5fa8c9918f7235f596873290bcd2516507a5d7e5ff518942918ce3ba88baa09a，状态REAL_252_504_ACCOUNT_VERIFIED；HOST实际退出0。正常/压力各504日的完整逐日经济内容及18项完整账户状态比较均PASS。
- 新连续参考公共VERIFICATION完成2段、1004.8597016999975秒；REPORT完成5段、3934.202528900001秒。六账户矩阵及各准备/核验/报告阶段合计收费81698.35410379997秒，实际证据4,650,756,721字节/52,707文件；这不是全研发累计，也不包含旧替代参考、前版尝试、资料维护或停机等待。
- 正式发布在写文件前误拒LONG_PUBLICATION_FIXED_RULE_CONFLICT。实测六份原请求与固定示例一致；正式V4 parser将指标实例从fast/slow/rsi/volatility排序为fast/rsi/slow/volatility，账户存的是正确规范化payload，发布器却直接比较原数组顺序。整数40/70同时规范化为浮点，但不是Python直接相等失败的主要原因。

### Testing

- 原harness完整执行并逐日读取原件比较，两个成本分支各504日PASS；六个账户独立核账及公共核验/双报告全部完成。没有用提前BASE诊断代替最终比较。
- 两项独立只读审查确认三个JOB各103项来源、六份策略/后端/入口以及能力目录37项闭包均不包含发布器；发布器本来就按独立当前SHA进入发布包。通过同一个正式V4解析器规范化原请求和固定示例后，六份原存payload完整相等。

### Notes

- 原SOURCE14的540归档文件、REAL、FROZEN、harness、账户/预算/失败历史保持原字节；必要的后验修复仅针对发布比较及其回归测试，按新的发布器SHA单独披露，不宣称它参加了原账户执行。
- 原生U8已完成，发布、最终CI、main合并同步和受管目录归档仍待完成；策略资格false，独立验证/Paper NOT_RUN。回滚点652eced，原件和消费不得删除或重开。

## 2026-10-10 - Task: 完成长周期原生验收及正式发布核验

### What was done

- SOURCE14 冻结运行真实完成 252/504 交易日的四个 BASE/STRESS 分段账户，以及 504 日两个独立连续参考账户。检查 4607 只，全部 3669 只资料合格股票参与，938 只逐项排除。504 日两个成本场景的完整逐日经济序列、6 项经济字段及 18 项完整状态与连续参考一致。REAL_ACCEPTANCE.json SHA 5fa8c9918f7235f596873290bcd2516507a5d7e5ff518942918ce3ba88baa09a；原生 HOST_EXIT 真实退出码 0。
- 仅修复发布器对正式 V4 parser 指标实例规范化次序的误拒：请求与固定例子使用正式解析，已经保存的账户规则继续按原字节内容完整哈希比较。修改 long_horizon_acceptance_publication_v1.py 与对应测试，不修改账户源码、冻结资料、原结果或消费记录。原 SOURCE14 540 文件归档全部保留；交付 539 项原字节及唯一发布器修复，分别绑定原验收和新发布器 SHA。
- 正式发布及公共 publication --long-horizon 均返回 PUBLISHED_METADATA_VERIFIED：6 账户，252/504 日，三项能力；凭证 SHA 8dfea24aba343d169de642214553dbb5b7deee768bac6fca86a20946a23e25c1。发布凭证及必要元数据存于 reports/long_horizon_universe_acceptance_v1；完整原件仍在主目录同名目录的 u8_v13_reference_retry1/actual。
- 增补 SOURCE14_POST_ACCEPTANCE_PUBLICATION_FIX_20261010.json 记录修复、原生验收和测试来源；主目录私有 PUBLICATION_FIX_DELIVERY_BINDING.json SHA b175b4bdc5afb60c96866a51fb7923b0bfdc2d63287d1e6e6ea73dc4707fb344 明确唯一差异，旧清单不改写。

### Testing

- 真实六账户及逐日独立核账、排序/漏斗/双报告、实际中断恢复和连续参考通过。成功矩阵累计主动资源 81698.3541038 秒，证据 4650756721 字节/52707 文件；并非整个研发过程的成本。所有旧失败、未知耗时保守收费及旧参考替换记录继续保留。
- 发布器模块 56 passed in 94.83s，绿色 XML SHA 346e7f7ee4a620db9fd4c3839b82dd294f4e982802a71b57e159cf23ec417175；冻结旧发布器对两项实际规范化请求真实误拒的红色 XML SHA 3509e8f15a1b7a38e9156927356e27dc553c32a256dfee324f6c4994604eb8a0。阈值、参数、评分、退出、说明和已存规则篡改仍拒绝。
- 发布器两文件及新安全同步 helper 获独立只读审查 PASS；helper 隔离 Git 场景 14 项通过，绑定最终 SHA 后另留验证记录。

### Notes

- 本轮为 HISTORICAL_MODELED 工程验证，独立策略验证/正式资格/Paper 均未完成，不将未来有效性写成已经证实。原市场可见时间模型及全窗口资料资格限制继续公开。
- 推送最终 HEAD、检查 CI、合并 PR36、安全同步主目录、重建前端及仅归档本任务 worktree 的交付操作接着执行，不能以本节预称已完成。
- 回滚点为主目录基线 786de0d307b770b6ac8f9a99e4349a588ed90ca1；通过版本回退停止新版派发，保留原件、预算、所有失败及同步备份，不删除资料或重开消费。

## 2026-10-10 - Task: 实现 U1–U8 持续全池研究工程接线

### What was done

- 按 docs/plans/2026-10-10-001-feat-continuous-universe-research-plan.md 实现明确总目标、Owner 内容绑定授权及增量、阶段资源储备、同任务多批接续和完整失败历史。普通候选由固定服务按总授权派生精确用途，旧消费、未知调用和超耗债务不清零。
- 公共 FULL_UNIVERSE_SUBMISSION_V4 复用新版全池检查、合格范围、V4 多指标及排序规则、正常/压力账户、独立核账与报告。每次 advance 至多派发一个受限 worker；暂停、到期、撤销后仅核对已派发的原请求，禁止新计算。
- 短期初筛、同规则最终 504 日探索复核及冻结后 252 日独立业务验证分开；新增固定 TRAIN 投影、未来登记证明、可信用途和物理来源/字节配额。独立访问史及结果不回流策略设计；正式资格与 Paper 另行判断。
- 增加受限模型网关客户端、原请求查询恢复、CLI/工作台/宿主统一控制、只读状态和结构化预检、存储边界及统一验收入口。默认模型无硬上限时明确等待，未调用真实收费服务。
- 新建 CONTINUOUS_UNIVERSE_RESEARCH_GUIDE.md、TRAIN_DATASET_PROJECTION_V1.md、CONTINUOUS_UNIVERSE_ACCEPTANCE.md 和 CONTINUOUS_UNIVERSE_DELIVERY_20261010.md，更新公共能力表和 CI 双平台 continuous suite。计划原文不改，真实交付门槛未达，保持 active。

### Testing

- 分模块工程回归已通过：公共 V4 真实受限 worker 6 项（222.90 秒）；U3 数据及投影/准入相关 68 项；模型网关 26 项；模型停派结算恢复 4 项；U6 19 项、U8 12 项；存储边界 2 项。先前两批真实公共 worker 回归 5 项（447.21 秒），完整最终源码回归和远端 CI 将另记最终结果。
- 能力文档生成检查 MATCHED；compileall 与 git diff --check 通过。所有模型及行情测试使用明确标记的合成替身和隔离原件，不是新真实模型/策略/独立验证证据。
- 独立审查发现的回执错归属、TRAIN 证明字段、登记确认绕过准入、异步自身暴露误判等已修复并回归；最终探索失败反馈接线和最后全套回归仍在本任务中继续完成。

### Notes

- 没有创建真实研究授权、扩容旧任务预算、读取封存行情或产生新策略资格。主目录现有合法 TRAIN 日历只有 462 日，缺 42 日及配套；真实受限模型网关与可信 252 日独立资料仍是外部条件，不能用合成测试补证。
- 开发目录为受管 worktree E:/worktrees/continuous-universe-research/trade-system-contract-port-v1。主目录的既有 progress.md 修改、行情、研究结果及未跟踪文件保持原样；最终 CI、推送、main 合并、安全同步和仅归档本 worktree 待交付操作完成后另记。
- 回滚点 78cfd99a489ecd450ce2fba27a2a6c99dd438e40；先停派并结清原请求，回退代码及部署，保留所有批准/预算/回执/暴露原件；回滚不退款或重新授予独立性。

## 2026-10-10 - Task: 补齐设计接手包的长期探索失败谱系

### What was done

- diagnosis_research_v4.py 的设计 handover 改为只读取 config、canonical 探索记录和预算；复用已核验的最终探索定性摘要，附消费计数，不再调用 confirmation.status。完整业务状态仍由普通 status 提供。
- docs/CONTINUOUS_UNIVERSE_RESEARCH_GUIDE.md 说明设计接手和完整状态的区别。原初筛记录、长期原件及所有独立结果保持不变。
- 为保留正在运行的冻结源码验收，后续交付在受管 E:/worktrees/continuous-universe-delivery/trade-system-contract-port-v1 独立目录进行；原 continuous-universe-research 目录仅继续原测试，最终两目录都须归档。

### Testing

- 新增红色回归先真实捕获设计 handover 调用 confirmation.status（1 failed in 4.09s）；修复后最终反馈及生命周期模块 15 passed in 62.47s，全部只用隔离合成 metadata，无真实模型、行情或授权。
- canonical 最终探索原件校验、独立敏感数据隔离、原件只读不变及 UTF-8 上下文上限均通过。能力文档生成 MATCHED，compileall 和 git diff --check 通过。
- Windows 本地全 continuous（排除已独立运行的 504 晋级及单列反馈文件）和 504 晋级真实受限 worker 仍在运行；远端双平台 CI 待推送后核对，尚未预称通过。

### Notes

- 代码基线回滚点仍为 78cfd99a489ecd450ce2fba27a2a6c99dd438e40；本小修可回退至 d4630453f289367ac84c3f2c4466281400e8fbf5，但该点故意保留红色 handover 回归，不应作为最终绿色交付。
- 实际模型与 504/252 日资料门槛未获得新证据，计划保持 active；没有签发真实批准、清除消费、启动后台研究或提升策略资格。

## 2026-10-10 - Task: 核实并修复持续研究安全扫描边界

### What was done
- 按 PR #37 的实际 SonarCloud 告警核对新路径：公共任务先校验 ID，再从固定任务目录重建 JOB 原件路径；最终探索与验收复用该归属检查。
- 新增 secure_file_reference_v1.py，统一绝对路径、普通文件、打开对象身份、可选归属边界、内容摘要与错误口径；可信部署与 Owner 的合法外部引用保持支持。TRAIN、独立快照和长周期恢复在开具资料范围或接触预算前核对路径。
- 模型上下文裁剪改为对现有记录的有限遍历；保留原先确定性裁剪顺序及全部累计计数。合成公共服务夹具对齐真实 task/account 目录，未放宽账户或业务断言。
- CI 增加共享安全读取的攻击与正常路径测试。原目录的冻结 504 日进程继续运行；所有修正仅在 delivery 工作区完成。

### Testing
- 安全读取、TRAIN 投影/准入及统一验收：89 passed、1 skipped；跳过为本机没有创建符号链接权限，文件对象替换攻击用例通过。
- 生命周期与恢复最终冻结版本：31 passed；最终探索反馈与独立业务验证：24 passed。能力文档生成检查 MATCHED，git diff --check 通过。
- 原实现 continuous 239 passed、独立接手回归 15 passed、前端 29 passed 与生产构建通过；修改后的公共账户及最终双平台 CI 尚在执行，不能将这些前序结果当作最终 CI 结论。

### Notes
- 不使用 NOSONAR、规则排除或状态伪造；等待修正版实际远端安全和兼容检查后再合并。
- 回滚点为合并前 78cfd99a489ecd450ce2fba27a2a6c99dd438e40；回滚保留已有授权、消费、未知请求、回执与暴露记录。主目录已有未提交研究资料原样保留，最终同步仅处理本次新增文件和追加记录。

## 2026-10-10 - Task: 在文件系统探测前拒绝越界恢复引用

### What was done
- 安全复扫剩余一项源于先解析路径后检查归属。共享引用读取改为先做纯词法归属校验；长周期恢复从规范 JOB 推导 root，再比较回执声明，不先探测自报目录。

### Testing
- 新增 validate/bytes/hash/json 四入口越界断言，禁止调用 resolve/lstat/open，修前四项失败、修后通过；共享读取与恢复最终回归 31 passed、1 skipped（本机符号链接权限）。git diff --check 通过，最终远端复扫及 CI 待执行。

### Notes
- 主目录同步程序另经只读审阅，改为快进明确批准的不可变合并 SHA，避免 origin/main 在预检后移动。该辅助程序在临时目录，不随产品发布。
- 回滚与审计保留要求沿用上一条，未改变规则、成本、账户算法或真实资料权限。

## 2026-10-10 - Task: 明确检查引用的全部父目录分量

### What was done
- 共享读取先核纯词法归属，再逐级检查已存在路径分量，拒绝父链接、末端链接、重解析点和非目录父分量；保留未创建尾部及固定资料缺失时的等待原因。
- 合法外部 Owner/deployment 引用、普通文件打开后的对象身份核验和内容摘要要求保持不变。

### Testing
- 最终共享读取、TRAIN 准入、生命周期和恢复联合回归：93 passed、1 skipped in 76.71s；跳过仍为本机创建符号链接权限，模拟链接及重解析分量拒绝测试通过。git diff --check 通过。
- 此次最终提交须重新通过远端安全扫描和双平台矩阵；不引用旧提交的绿色状态替代。

### Notes
- 目录分量校验仍依赖既有受保护目录权限，不宣称替代操作系统目录句柄锁。没有修改撮合、账户算法、策略门槛或真实数据访问范围。
- 回滚到合并前 78cfd99a489ecd450ce2fba27a2a6c99dd438e40 时继续保留授权、消费、回执和暴露原件。

## 2026-10-10 - Task: 用同一份进度原件核对并记录恢复证据

### What was done
- 长周期恢复通过安全读取一次取得已归属任务的 checkpoint 或 feature 原件；只有文件确实缺失视为未产生，越界、目录及其他读取错误继续阻断。
- 状态校验与 RESUME 摘要使用同一份原始字节，取消先查存在性再读取、随后另读文件算哈希的竞态。

### Testing
- 进度读取边界、账户恢复及长期故障恢复：23 passed in 66.95s。既有语义保留：未知消费沿原用途结清，不能免费重试或重置预算。
- 最终远端安全与双平台检查需在此次提交重新验证；本条不声明提前通过。

### Notes
- 没有修改撮合、费用、策略规则或合格条件。恢复缺失原件和损坏原件仍是两种不同状态。
- 回滚点与原件保留要求沿用合并前 78cfd99a489ecd450ce2fba27a2a6c99dd438e40。

## 2026-10-10 - Task: 在原生文件打开前再次核对目录边界

### What was done
- 共享读取在最终 os.open 前对原生路径和固定目录规范化，按目录加分隔符核对归属，再打开同一已核验字符串；合法外部原件仍以已逐级核验的父目录为边界。
- 保留原先词法归属先于文件系统探测、全部父分量链接拒绝和打开对象身份复核，未增加规则排除或跳过安全扫描。

### Testing
- 新增 5 项原生边界回归修前失败、修后通过；共享读取最终 38 passed、1 skipped，跳过为本机符号链接权限。中文空格路径、驱动器根目录及同前缀兄弟目录均有明确覆盖。
- TRAIN 准入、生命周期与中断恢复联合回归正在同一冻结源码执行；最终远端安全扫描与双平台 CI 须在本次提交核对，尚未声明通过。

### Notes
- 未修改策略、撮合、费用或真实资料权限。主目录同步将保留原有未提交研究文件和完整进度字节。
- 回滚点仍为合并前 78cfd99a489ecd450ce2fba27a2a6c99dd438e40，回滚保留全部授权、消费、未知请求和暴露原件。
