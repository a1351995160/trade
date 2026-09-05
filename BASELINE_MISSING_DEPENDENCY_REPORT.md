# Baseline Missing Dependency Report V1

## 审计范围

本报告记录 `BASELINE_COMPLETENESS_REPAIR_V1` 的事实确认结果。目标是补齐当前 Research Factory 基线对未提交工作区文件的运行时依赖，不新增研究能力，不创建或修改 Objective、Candidate、Trial、Budget 或研究运行状态。

- 源仓库：`E:\llmwiki\chanlun-trading-system`
- 修复前固定基线：`3a7db558cb6f01b81bae33cd1e95bca987ce00fa`
- 修复分支：`codex/research-factory-baseline-v1`
- 修复提交：本报告所在的第六个基线提交；不可变提交哈希以提交完成后的 Git HEAD 和 `progress.md` 记录为准。

## 干净检出证据

在不触碰源工作区 dirty/untracked 文件的前提下，从修复前固定基线建立了独立干净检出：

1. `python -m compileall -q src` 通过。
2. `python -m pytest --collect-only -q` 只完成部分收集：`524 tests collected`，随后出现 `6` 个收集错误。
3. 错误集中为两个不在固定基线中的模块：
   - `chanlun_trader.research_console`
   - `run_codex_guided_autonomous_research_pilot_v1`
4. 将工作区已有的 `research_console.py` 临时放入诊断检出后，收集错误只剩 legacy pilot 测试的导入依赖。
5. 将该 legacy 测试改为显式 `pytest.importorskip` 后，诊断检出达到 `575 tests collected`、`0` 个导入错误，legacy pilot 测试 `1 skipped`。
6. 运行期测试进一步确认 `docs/CODEX_RESEARCH_PROMPT_V1.md` 是 Codex backend 的正式版本化输入；它虽不在 collection 阶段触发导入错误，但缺失会使 Codex governance tests 失败，因此作为小型正式设计文档补入闭包。

这证明问题是基线完整性缺口，而不是需要把整个 dirty/untracked 工作区纳入版本库。

## 缺口闭包

| 路径 | 固定基线 | dirty 工作区 | 被谁依赖 | 分类 | 本次处理 | 原因 |
|---|---:|---:|---|---|---|---|
| `src/chanlun_trader/research_console.py` | 缺失 | 存在 | Web API、Console 测试、Research Factory 的 Console 读取边界测试 | `CANONICAL_RUNTIME` | 纳入 | 它是 Research Console 的实际只读/治理边界实现；不纳入会导致 Web API 和相关测试在干净检出中无法导入。 |
| `docs/CODEX_RESEARCH_PROMPT_V1.md` | 缺失 | 存在 | `src/chanlun_trader/research_factory/codex_backend.py` 和 Codex backend tests | `CANONICAL_RUNTIME` | 纳入 | 这是冻结、版本化的研究设计 prompt 输入，不是运行报告或研究数据；缺失会造成正式 Codex backend 在运行期无法读取 prompt。 |
| `tests/research_factory/test_raw_bootstrap_validation_audit_v1.py` | 存在 | 修改 | 该测试自身导入 legacy pilot helper | `TEST_BOUNDARY` | 纳入本次修改 | 使用 `pytest.importorskip` 将遗留自治 pilot 集成依赖明确隔离；基线不再因可选的历史 pilot 文件缺失而无法收集测试。 |
| `scripts/run_codex_guided_autonomous_research_pilot_v1.py` | 缺失 | 存在 | 仅被上述 legacy 集成测试直接导入 | `LEGACY_ONLY` | 不纳入 | 该脚本会创建/加载 Objective、生成运行报告，并调用真实 Factory orchestrator 路径；它不是 Console 或 Research Factory 核心运行时的必要依赖，纳入会扩大基线边界并引入自动研究执行风险。 |

在候选文件临时加入诊断检出后，静态导入闭包未发现第三个缺失的源代码、配置或 schema 依赖；运行期进一步发现并确认了上表中的正式 prompt 文档依赖。固定基线中已存在的直接依赖保持原状。

## `research_console.py` 审核结论

该模块属于应进入基线的 canonical runtime：

- 提供 Research Console 的只读读取模型和治理视图，承接 Web API 使用的 Proposal、Objective、AI Design、Candidate、Structural 等边界。
- 通过 `PredictiveTrialStartServiceV1(..., auto_run=False)` 保持 Console 不自动运行 Trial。
- 未发现 subprocess、`os.system` 或直接调用 Trial runner 的执行路径。
- 运行时写入仅限 AI invocation mode 的治理配置原子更新；研究状态、预算、Candidate、Trial 和 Performance artifact 不在本次导入闭包中新增。
- 路径使用项目相对的研究数据/报告目录；未发现本机绝对路径、私钥、密钥、token、cookie 或账号密码。
- 文件为文本源代码，大小约 `221,237` 字节，未达到大体积二进制或运行数据特征。

因此它是“补齐现有基线依赖”，不是新功能扩展。

## autonomous pilot 排除结论

`scripts/run_codex_guided_autonomous_research_pilot_v1.py` 保留在源工作区但不进入本次基线。它属于历史/实验性自治 pilot：包含预声明批次、Objective 物化、报告输出和 `AIResearchFactoryOrchestratorV1.run_real` 调用能力。将它作为缺失依赖提交，会把“可导入的核心基线”错误扩大为“可执行自治研究 pilot”，违反本轮不启动研究任务、不纳入运行产物的边界。

对应测试只验证 raw bootstrap failure view/report classification，并不验证基线核心治理生命周期；因此测试边界使用可见且可解释的 optional import skip，而不是把 pilot 反向提升为正式依赖。

## 提交边界

本次第六个提交只允许包含以下四项：

1. `src/chanlun_trader/research_console.py`
2. `docs/CODEX_RESEARCH_PROMPT_V1.md`
3. `tests/research_factory/test_raw_bootstrap_validation_audit_v1.py`
4. `BASELINE_MISSING_DEPENDENCY_REPORT.md`

明确排除：

- `data/`、`reports/`、`research_ai_staging/`、`deliverables/`、`tmp/`
- Parquet、SQLite、DB、JSONL、日志、PID、截图、ZIP 和构建缓存
- `scripts/run_codex_guided_autonomous_research_pilot_v1.py`
- `data/research/research_factory/batches/AI_HANDOFF_V2_e1ddf/durable_frozen_candidate_contracts.json` 等真实 frozen/runtime data；它们仅用于部分运行期集成测试，不属于 source baseline。
- 源工作区已有的 `progress.md` 历史 dirty 变化
- 任何 Objective、Candidate、Trial、Performance、Budget 或 daemon/orchestrator runtime artifact

## 验证结论

修复提交前的诊断检出已验证：

- `python -m compileall -q src`：通过
- `python -m pytest --collect-only -q`：`575 collected`、`0 import errors`、`1 legacy pilot skip`
- 候选 Console/WebApp/AI Design/Proposal/raw bootstrap 测试：候选文件加入后可收集；Codex prompt 补入后其 prompt 依赖闭合。剩余运行期失败仅因干净检出没有本地 unversioned Objective、frozen contract、Structural preview 或事件分区 artifact，分类为 `INTEGRATION_LOCAL_WORKSPACE_ONLY`，不复制或提交这些运行产物。

新完整基线的最终提交哈希、干净检出回归结果和未提交本地文件分类，在提交完成后写入本轮 `progress.md` 末尾并在交接回复中报告。
