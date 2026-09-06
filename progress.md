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
