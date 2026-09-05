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
