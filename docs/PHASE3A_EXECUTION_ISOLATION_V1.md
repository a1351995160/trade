# P3-A 执行隔离与安全启动验收

## 范围与基线

仅实施 P3-A。[总体规划](AUTONOMOUS_RESEARCH_MASTER_ROADMAP_V1.md)不是运行授权或 canonical 状态源，其他工作包仍为 PLANNED。

- BASE_COMMIT：7f22237958731fbc5e7803ee41ff40a55a277ff1
- BRANCH：codex/phase3a-execution-isolation-v1
- 独立 worktree：E:\llmwiki\chanlun-trading-system-phase3a-execution-isolation-v1
- PHASE3_CLOSED=false；MAIN_MERGED=false；NEXT_PACKAGE_STARTED=false。

远端 a1351995160/trade 的实际 main 已核对。原始研究目录仅读取 Git 元数据、尝试读取不存在的 AGENTS.md；未读取研究工件、绩效或 Final Test，未独立核验真实运行态。

## 源码依据与实现

基线 webapp.py:51–69 构造 PROJECT_ROOT 全局服务；:72–79 启动五条恢复；:148–191 四个 helper 可回退全局服务；:477 的 rescan 使用 PROJECT_ROOT；:293 创建回测线程。config.py:9 定义的 PROJECT_ROOT 是源码目录。predictive_trial_start.py:223 与 predictive_trial_reauthorization.py:63 默认 auto_run=True。原 Web 无统一只读策略。

现通过 create_app(research_root, execution_policy) 显式组合服务，将 root、服务、缓存和任务状态归属应用实例。未绑定 root 的 webapp:app 仅提供页面与配置，业务请求要求 RESEARCH_WORKSPACE_REQUIRED。scripts/run_ui.py 保留位置端口参数，新增 --research-root，始终使用默认只读策略。源码与前端资源目录保持分离。

删除 Web startup recovery handler，不改变五个领域恢复方法及其算法。状态固定说明 RECOVERY_DISABLED，不为提示调用恢复。Predictive 服务组合显式 auto_run=False，Web start/resume 全部拒绝；CP 原 Phase 2 预测门禁不变。

探针定位了三个隐藏目录写入：io_safety.py 模块导入、OrchestratorStoreV2 构造、DaemonCheckpointStoreV1 构造。分别把建目录移至既有显式审计/事件写入，未改恢复、锁、状态机、预算或阈值。get_operations 缺失 canonical 数据时返回 ORCHESTRATOR_SOURCE_UNAVAILABLE，不自动物化。

## 模式与工作区

| 配置 | 读取 | 写入 |
|---|---|---|
| 缺省且无 root | 页面、配置 | 研究入口要求绑定工作区 |
| READ_ONLY / 显式 root | 只读投影、inspect、明确 dry_run=true 的 tick | 其他 POST 在领域调用前拒绝 |
| 未知或缺失 mode | 同只读 | 不提升能力 |
| GOVERNED / EXTERNAL | 只读 | 不允许外部工作区副作用 |
| GOVERNED / SYNTHETIC | 只读 | 进入既有领域服务，仍检查确认、身份和状态 |

ExecutionPolicy 为不可变 dataclass，环境变量不能提升默认应用权限。allow_structural、allow_process_start 默认 False，是额外运行约束，不替代人工确认。既有分发测试显式允许合成执行端并保留原领域断言。CREATE_AND_ACTIVATE 按领域同样的大写规则在确认写入前拦截，覆盖缺省及小写输入。本机访问限制保留，本机访问不等于授权。

非法、缺失 synthetic root、相对路径、父级跳转和文件路径均拒绝。SYNTHETIC 创建及请求前拒绝 root、祖先和子树中的符号链接 / Windows reparse point（含 junction），不跟随子树链接。测试覆盖根链接、子目录链接、创建后插入链接。各服务无全局 root fallback。

这是应用控制，不是 OS/网络物理沙箱。不承诺防御持有进程内代码执行权的调用者、预置硬链接或恶意并发进程在路径校验后替换目录。合成目录须由可信测试创建。

legacy Web screener/backtest/kline 保留实现，但本轮策略拒绝未完成业务 root 注入的入口，避免行情、缓存、报告和线程副作用；CLI 路径留待后续。

## 测试与计数

新建 .venv 使用 Python 3.13。清华镜像 TLS 失败后改用官方 PyPI 安装 requirements.txt 和 pytest，未复制环境依赖或关闭 TLS。依赖安装允许网络，业务测试另行隔离。

隔离设置：PYTHONDONTWRITEBYTECODE=1；PYTHONPATH 包含 tests/isolation 和 src；CHANLUN_TEST_ISOLATION=1；CHANLUN_PROTECTED_ROOT 指向禁止访问的参考根。sitecustomize 在 import 前拦截受保护目录内容访问/扫描、业务网络和非 Python 进程。Windows asyncio 内部 socketpair 保留。pytest 另拦截真实 Predictive.execute、Structural.build、Codex AI.invoke；合成子进程继承环境。它不是恶意 Python 子进程的物理沙箱。

| 本地验证 | 实际结果 |
|---|---|
| P3-A | 66 passed，0 failed/skipped/deselected |
| Phase 1 原选择 | 235 passed，12 deselected，0 failed/skipped |
| Phase 2 原选择 | 24 passed，0 failed/skipped/deselected |
| collect-only | 740 collected，另 1 个既有 legacy integration 模块 skipped |
| compileall | PASS |
| npm ci / npm run build | PASS；既有大 chunk 提示 |
| npm test | 8 passed，0 failed/skipped；Node 24.15.0 |
| git diff --check | PASS |

精确 Phase 1/2 选择见 workflows，未扩展原 12 个排除项。collected 不代表 passed。既有 Web 测试改为 synthetic app.state 注入，保留确认和身份断言；默认 K 线测试改为验证策略拒绝，不再探测本机行情，这是兼容变化。一个 Orchestrator fixture 显式创建待写入目录，不再依赖构造副作用。

新 fixture 自包含。待恢复 Trial 的合同由合成构造器生成，政策由默认政策构造器生成，通过真实 confirmation 创建 auto_run=False 的临时注册、预算与 intent；不复制历史运行工件、不改 receipt、不运行预测执行器。startup 前后完整文件集合/哈希不变。另覆盖有效 AI design 缺少状态投影，读取仍不恢复。

全部研究 GET 通过文件写入、os.open 和目录变更拦截，结合前后哈希；每条 POST 在只读模式用禁止领域调用的探针验证。双 app 的服务、缓存和任务表独立，A 的合法人工确认不改变 B。参考目录与 sentinel 均在 tmp_path，从未对真实研究工件计算哈希。

| 套件 | 合成模板调用 | approve 尝试 | confirm 尝试 | 真实 Predictive / Structural / Codex AI |
|---|---:|---:|---:|---|
| P3-A | 5 | 1 | 4 | 0 / 0 / 0 |
| Phase 1 | 72 | 70 | 74 | 0 / 0 / 0 |
| Phase 2 | 19 | 17 | 17 | 0 / 0 / 0 |

计数包含 fixture 和失败尝试，不等于成功动作数量。业务网络、非 Python 进程和受保护目录访问探针均为 0。这些只描述本轮覆盖，真实系统历史计数为 NOT_VERIFIED。真实研究、订单及 Final Test 内容读取均未执行，真实运行态未独立核验。

## CI 与交接

新增 Phase 3A Execution Isolation Certification，复用 Phase 1/2 原选择；旧 workflow 同步先验证 import/startup，再编译/收集/回归。仅依赖安装步骤关闭测试网络探针。无 schedule、部署、业务凭据或真实研究目录挂载。

CI_STATUS_AT_COMMIT=PENDING。精确 NEW_COMMIT、run_id/head_sha 与最终实际状态由交付回复提供；本地通过不冒充 CI 通过。[GitHub Actions](https://github.com/a1351995160/trade/actions)。完成分支认证后交独立复核，不合并 main。

## 未覆盖项

- P3-B：公共重启恢复、Action/dry-run replay 与跨进程互斥；本轮仅控制 Web 是否调用恢复。
- P3-C：全生命周期多 tick E2E 及多入口授权一致性。
- R1：依赖真实工件的 legacy 集成测试和运行数据闭包；未执行全量测试，未核验真实运行态。
- CLI/Daemon 全量权限、legacy Web 执行注入与 OS 物理隔离未在本轮认证。

## 入口矩阵

研究 root 统一来自 app.state.services；静态页面和配置来自源码目录。“治理”表示进入原领域门禁，绝不表示批准。下表行号对应本轮 webapp.py。

| 方法 | 入口 | READ_ONLY | GOVERNED + SYNTHETIC | 源码行 |
|---|---|---|---|---|
| GET | / | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:146 |
| GET | /api/config | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:154 |
| POST | /api/screener | LEGACY_EXECUTION_DISABLED | LEGACY_EXECUTION_DISABLED | webapp.py:167 |
| POST | /api/backtest | LEGACY_EXECUTION_DISABLED | LEGACY_EXECUTION_DISABLED | webapp.py:182 |
| GET | /api/backtest/status/{task_id} | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:240 |
| GET | /api/kline/{code} | LEGACY_EXECUTION_DISABLED | LEGACY_EXECUTION_DISABLED | webapp.py:248 |
| GET | /api/research-console/objectives | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:341 |
| GET | /api/research-console/ai-invocation-mode | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:346 |
| POST | /api/research-console/ai-invocation-mode | 拒绝 | 治理；另受执行策略限制 | webapp.py:351 |
| GET | /api/research-console/{objective_id}/dashboard | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:358 |
| GET | /api/research-console/{objective_id}/daemon | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:363 |
| GET | /api/research-console/{objective_id}/orchestrator | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:368 |
| GET | /api/research-console/{objective_id}/orchestrator/events | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:373 |
| GET | /api/research-console/{objective_id}/ai-status | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:378 |
| GET | /api/research-console/{objective_id}/manual-ai-handoff | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:383 |
| GET/HEAD | /api/research-console/{objective_id}/safe-runtime-context | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:388 |
| GET | /api/research-console/{objective_id}/autonomous-control-plane | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:393 |
| POST | /api/research-console/{objective_id}/autonomous-control-plane/tick | 拒绝（dry_run=true tick 可读） | 治理；另受执行策略限制 | webapp.py:398 |
| GET | /api/research-console/{objective_id}/ai-tasks | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:405 |
| GET | /api/research-console/{objective_id}/ai-results | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:410 |
| POST | /api/research-console/{objective_id}/manual-ai-handoff/rescan | 拒绝 | 治理；另受执行策略限制 | webapp.py:415 |
| GET | /api/research-console/{objective_id}/closeout | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:423 |
| GET | /api/research-console/{objective_id}/governance-decision | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:428 |
| GET | /api/research-console/{objective_id}/governance/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:433 |
| POST | /api/research-console/{objective_id}/governance/preview | 拒绝 | 治理；另受执行策略限制 | webapp.py:440 |
| POST | /api/research-console/{objective_id}/governance/confirm | 拒绝 | 治理；另受执行策略限制 | webapp.py:447 |
| GET | /api/research-console/{objective_id}/governance/execution/{execution_id} | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:460 |
| GET | /api/research-console/{objective_id}/governance/execution/{execution_id}/receipt.json | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:465 |
| GET | /api/research-console/{objective_id}/operations | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:471 |
| POST | /api/research-console/{objective_id}/orchestrator/pause | 拒绝 | 治理；另受执行策略限制 | webapp.py:482 |
| POST | /api/research-console/{objective_id}/orchestrator/resume | 拒绝 | 治理；另受执行策略限制 | webapp.py:487 |
| POST | /api/research-console/{objective_id}/orchestrator/stop | 拒绝 | 治理；另受执行策略限制 | webapp.py:492 |
| POST | /api/research-console/{objective_id}/orchestrator/recover | 拒绝 | 治理；另受执行策略限制 | webapp.py:497 |
| POST | /api/research-console/{objective_id}/orchestrator/start-ai | 拒绝 | 治理；另受执行策略限制 | webapp.py:502 |
| POST | /api/research-console/{objective_id}/governance-decision | 拒绝 | 治理；另受执行策略限制 | webapp.py:507 |
| GET | /api/research-console/{objective_id}/daemon/health | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:516 |
| GET | /api/research-console/{objective_id}/pipeline | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:521 |
| GET | /api/research-console/{objective_id}/predictive/authorization/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:526 |
| POST | /api/research-console/{objective_id}/predictive/authorize | 拒绝 | 治理；另受执行策略限制 | webapp.py:531 |
| GET | /api/research-console/{objective_id}/predictive/trial/start/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:538 |
| POST | /api/research-console/{objective_id}/predictive/trial/start | 拒绝 | 预测执行禁止 | webapp.py:543 |
| GET | /api/research-console/{objective_id}/predictive/trial/resume/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:549 |
| POST | /api/research-console/{objective_id}/predictive/trial/resume | 拒绝 | 预测执行禁止 | webapp.py:554 |
| GET | /api/research-console/{objective_id}/predictive/trial/new/authorization/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:560 |
| POST | /api/research-console/{objective_id}/predictive/trial/new/authorize | 拒绝 | 治理；另受执行策略限制 | webapp.py:565 |
| GET | /api/research-console/{objective_id}/predictive/trial/new/start/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:571 |
| POST | /api/research-console/{objective_id}/predictive/trial/new/start | 拒绝 | 预测执行禁止 | webapp.py:576 |
| POST | /api/research-console/{objective_id}/structural/reconcile | 拒绝 | 治理；另受执行策略限制 | webapp.py:582 |
| GET | /api/research-console/{objective_id}/structural/readiness | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:596 |
| GET | /api/research-console/{objective_id}/structural/reconciliation | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:601 |
| POST | /api/research-console/{objective_id}/structural/start | 拒绝 | 治理；另受执行策略限制 | webapp.py:606 |
| POST | /api/research-console/{objective_id}/structural/projection-repair | 拒绝 | 治理；另受执行策略限制 | webapp.py:620 |
| GET | /api/research-console/{objective_id}/candidates/{candidate_id}/contract-correction/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:645 |
| POST | /api/research-console/{objective_id}/candidates/{candidate_id}/contract-correction/confirm | 拒绝 | 治理；另受执行策略限制 | webapp.py:673 |
| GET | /api/research-console/{objective_id}/candidates | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:698 |
| GET | /api/research-console/{objective_id}/candidates/{candidate_id} | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:703 |
| GET | /api/research-console/{objective_id}/candidates/{candidate_id}/structural | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:708 |
| GET | /api/research-console/{objective_id}/trials | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:713 |
| GET | /api/research-console/{objective_id}/trials/{trial_id} | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:718 |
| GET | /api/research-console/{objective_id}/trials/{trial_id}/reconciliation/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:735 |
| POST | /api/research-console/{objective_id}/trials/{trial_id}/reconciliation/confirm | 拒绝 | 治理；另受执行策略限制 | webapp.py:755 |
| GET | /api/research-console/{objective_id}/budget | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:777 |
| GET | /api/research-console/{objective_id}/handoff | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:782 |
| GET | /api/research-console/{objective_id}/shadow/latest | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:787 |
| GET | /api/research-console/{objective_id}/data-health | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:792 |
| GET | /api/research-console/{objective_id}/evolution | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:797 |
| GET | /api/research-console/{objective_id}/evolution/proposals | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:802 |
| GET | /api/research-console/{objective_id}/evolution/ai-design | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:807 |
| GET | /api/research-console/{objective_id}/evolution/ai-design/approval | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:812 |
| POST | /api/research-console/{objective_id}/evolution/ai-design/approval | 拒绝 | 治理；另受执行策略限制 | webapp.py:817 |
| GET | /api/research-console/{objective_id}/candidate-proposals | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:823 |
| POST | /api/research-console/{objective_id}/candidate-proposals/generate | 拒绝 | 治理；另受执行策略限制 | webapp.py:828 |
| GET | /api/research-console/{objective_id}/candidate-proposals/materialization | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:834 |
| POST | /api/research-console/{objective_id}/candidate-proposals/materialization/preview | 拒绝 | 治理；另受执行策略限制 | webapp.py:839 |
| POST | /api/research-console/{objective_id}/candidate-proposals/materialization/confirm | 拒绝 | 治理；另受执行策略限制 | webapp.py:847 |
| GET | /api/research/evolution/proposals | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:857 |
| GET | /api/research/evolution/proposals/{proposal_id} | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:863 |
| GET | /research/evolution/proposals/{proposal_id} | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:863 |
| POST | /api/research/evolution/proposals/{proposal_id}/review | 拒绝 | 治理；另受执行策略限制 | webapp.py:869 |
| POST | /research/evolution/proposals/{proposal_id}/review | 拒绝 | 治理；另受执行策略限制 | webapp.py:869 |
| POST | /api/research/evolution/proposals/{proposal_id}/close | 拒绝 | 治理；另受执行策略限制 | webapp.py:876 |
| POST | /research/evolution/proposals/{proposal_id}/close | 拒绝 | 治理；另受执行策略限制 | webapp.py:876 |
| GET | /api/research/evolution/proposals/{proposal_id}/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:888 |
| GET | /research/evolution/proposals/{proposal_id}/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:888 |
| POST | /api/research/evolution/proposals/{proposal_id}/confirm | 拒绝 | 治理；另受执行策略限制 | webapp.py:894 |
| POST | /research/evolution/proposals/{proposal_id}/confirm | 拒绝 | 治理；另受执行策略限制 | webapp.py:894 |
| GET | /api/research/candidates/proposals | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:900 |
| GET | /api/research/candidates/proposals/{proposal_id}/freeze-preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:906 |
| GET | /research/candidates/proposals/{proposal_id}/freeze-preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:906 |
| GET | /api/research/candidates/proposals/{proposal_id}/materialization | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:912 |
| GET | /research/candidates/proposals/{proposal_id}/materialization | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:912 |
| GET | /api/research/candidates/proposals/{proposal_id}/materialization/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:919 |
| GET | /research/candidates/proposals/{proposal_id}/materialization/preview | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:919 |
| POST | /api/research/candidates/proposals/{proposal_id}/materialization/preview | 拒绝 | 治理；另受执行策略限制 | webapp.py:927 |
| POST | /research/candidates/proposals/{proposal_id}/materialization/preview | 拒绝 | 治理；另受执行策略限制 | webapp.py:927 |
| POST | /api/research/candidates/proposals/{proposal_id}/materialization/confirm | 拒绝 | 治理；另受执行策略限制 | webapp.py:935 |
| POST | /research/candidates/proposals/{proposal_id}/materialization/confirm | 拒绝 | 治理；另受执行策略限制 | webapp.py:935 |
| GET | /api/research/candidates/proposals/{proposal_id} | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:943 |
| GET | /research/candidates/proposals/{proposal_id} | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:943 |
| POST | /api/research/candidates/proposals/{proposal_id}/review | 拒绝 | 治理；另受执行策略限制 | webapp.py:948 |
| POST | /api/research/candidates/proposals/{proposal_id}/freeze | 拒绝 | 治理；另受执行策略限制 | webapp.py:955 |
| POST | /research/candidates/proposals/{proposal_id}/freeze | 拒绝 | 治理；另受执行策略限制 | webapp.py:955 |
| POST | /api/research/candidates/proposals/{proposal_id}/close | 拒绝 | 治理；另受执行策略限制 | webapp.py:961 |
| GET | /api/research-console/{objective_id}/reports | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:972 |
| GET | /api/research-console/{objective_id}/reports/{report_id} | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:977 |
| GET | /api/research-console/{objective_id}/governance | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:982 |
| GET | /{frontend_path:path} | 只读；缺源明确不可用 | 只读；缺源明确不可用 | webapp.py:987 |
