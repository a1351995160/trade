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
