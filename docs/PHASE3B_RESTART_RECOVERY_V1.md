# P3-B 公共重启恢复与写入互斥 V1

状态：本地实现与验证完成，独立复核待进行。分支 CI 必须以最终提交 SHA 的远端记录为准；本文件提交时为 PENDING。P3-C 未开始，Phase 3 未关闭，不执行真实研究。

基线：`7e277dbe1d20061fd672cf53d1358d07f16a0b1b`（P3-A PR #3 已合并）；P3-A 最终认证 HEAD：`21d12ba08422849f32aef88c330cac7d3787d1b1`。独立分支：`codex/phase3b-restart-recovery-v1`。

## 入口与执行顺序

`AutonomousResearchControlPlaneV1(root, execution_policy=...).recover(objective_id, dry_run=False)` 是显式恢复入口；`tick(objective_id)` 也先发现待恢复项，再决定本轮是否执行新动作。调用方不需要保存旧 Action。每轮至多一个领域动作；恢复完成后停止本轮，不顺带执行下一阶段。

默认 ExecutionPolicy 为 READ_ONLY。写 tick、直接 execute_action、recover 均要求显式 GOVERNED + SYNTHETIC，且 research root 必须为系统临时目录的严格子目录，通过既有 root 校验。CLI 使用既有模块入口，`--recover --objective-id <id> --root <synthetic-root> --governed-synthetic` 显式恢复；未提供可写策略不授权写入。Web 构造传递策略，startup 与 GET 保持只读；没有新增 startup recovery，也没有自动批准。

1. 在 Objective 内核锁内检查 journal 与 intent 的完整性、对应关系和唯一 pending 项。
2. 验证原 Action 的确定性身份、能力、所需状态、候选身份、预算与 outcome-blind 字段；外部 Mapping 不允许多余或非 canonical 字段。哈希是完整性校验，不是权限凭证。
3. 先匹配该 Action 的 canonical 副作用。精确匹配则只追加 COMPLETED，不重调 provider，不要求当前规划仍指向旧动作。
4. 没有副作用时重新规划并比较当前合法 Action、人工确认和权限；仍新鲜才用原 idempotency key 重试，过期、撤销、错身份一律拒绝。
5. provider 返回后还必须匹配 canonical 证据才能完成；返回值本身不是完成证明。

## 持久协议与旧记录

写入顺序为初始化证据标记 → 不可变 intent → STARTED → 领域调用 → canonical 匹配 → COMPLETED。intent 文件为 `intents/<action-id-hash>.json`，schema 为 `autonomous-execution-intent-v1`；新 receipt 为 v2，并绑定 `execution_intent_hash`。写入 flush/fsync，POSIX 同步目录。intent 保存原执行意图，不建立第二套 canonical 业务权威。

旧 v1 COMPLETED 仍可原样回放；旧未完成记录缺少可验证 intent 时阻断。半行、空 journal、标记存在但 journal 丢失、intent 错配、孤立 intent、多 pending、非法状态或绩效泄漏均 fail closed，不自动修复、补造或隔离文件。完整目录及所有检测标记同时被外部删除、恶意重写全部历史、备份回退不属于本轮可证明的防篡改能力；没有引入签名或外部日志权威。

## 写入边界矩阵

| 路径 | 本轮处理 |
| --- | --- |
| CP tick / execute_action / recover、journal 变更 | 同一规范化 root + Objective 内核锁；检查与提交在锁内 |
| AI design generate/recover/approval | Objective 锁；原治理规则保留 |
| Proposal generate/recover/review/freeze/close | Objective 锁；proposal 反查 Objective 后在锁内复核 |
| Materialization preview/confirm/recover | Objective 锁；confirm/recover 追加共享 registry 边界 |
| Structural entry、canonical structural persist/reconcile、projection apply、contract correction | Objective 锁；原人工门禁保留；projection apply=False 只读 |
| canonical daemon accept_ai_batch/run_once 与 Orchestrator ingest/save/set/recover/run_ai | 对 CP 管理 Objective 拒绝旁路写入，要求显式服务入口；没有重写整个历史调度器 |
| Durable contract registry.write | 同一文件资源锁内 reload/merge/write |
| Artifact graph.add_node/add_edge | 同一文件资源锁内 reload/merge/write，防止不同 Objective 的旧快照覆盖 |

Objective 锁使用规范化绝对 root 与身份；Windows 使用 `msvcrt.locking`，Linux 使用 `flock`。锁文件不删除、不重命名，内核随进程退出释放；不依赖 TTL 或 PID 存活猜测。同线程嵌套可重入，不同线程/进程 BUSY；旧 owner release 不能释放新 owner。资源锁按文件位置共享。历史 daemon 调度锁仍存在，但上述相关写入口先受新增边界约束；不声称全部历史后台调度已改造。

## dry-run

inspect、tick(dry_run=True)、execute_action(dry_run=True)、recover(dry_run=True) 使用不创建文件的锁探测和前后证据快照。BUSY、损坏、错身份、过期、可恢复等分支均不写。测试同时比较目录、内容哈希、mtime，并拦截 mkdir、写模式 open、journal append 和 provider 调用，不能只凭返回码判断只读。

## 本地验证矩阵

所有运行验证在全新 venv、临时 synthetic workspace 完成，未运行真实研究、真实 provider、Final Test 或真实预测。

| 验证 | 本地结果 |
| --- | --- |
| P3-A import/startup 隔离 | 66 passed |
| Phase 1 确定性回归 | 235 passed，12 deselected（沿用 P3-A 排除列表） |
| Phase 2 | 24 passed |
| P3-B | 33 passed |
| 最后修改后的 P3-B + CP + P3-A 定向复跑 | 116 passed |
| 全量 collect | 773 collected，1 legacy module skipped；不计为通过 |
| compileall / diff --check | 通过 |
| 前端测试 / 构建 | 8 passed / 通过；保留既有 bundle 大小警告 |

P3-B 包括真实子进程 os._exit/kill、三阶段副作用后重启、前调用退出后同 key 重试与 stale 拒绝、CP/领域竞争、两恢复进程竞争、owner 死亡、线程重入、独立 Objective、规范化 root、共享 graph 旧快照竞争、旧完成回放、Action 重哈希伪造、撤销确认、证据损坏以及 CLI/Web 入口。硬退出 worker 通过已 fsync 的调用证据记录检查，不把未运行的 atexit 探针当作零次。

本地平台：Windows、Python 3.13.5、Node 24.15.0；源码 worktree 在 E: exFAT，实际并发与恢复 fixture 在 C: NTFS 临时目录。不能把源码盘文件系统当成运行认证文件系统。CI 工作流在 Ubuntu/Windows、Python 3.11 上记录平台和临时目录文件系统后运行同样矩阵。

最终全新 venv 验证中 predictive/structural/AI 禁用执行器调用、网络、非测试进程调用与受保护目录访问探针均为 0。早期系统 Python 的四次启动各有两次 distribution discovery 访问被隔离钩子阻止（共 8 次），未读到内容；改用全新 venv 后消失。不能把早期阻止事件抹成零。受保护目录写入未观察到。

## 未验证与交接

首次分支工作流 `34211579523` 在配置解析时拒绝 job-level env 中的 `runner.temp`，没有运行测试。已改为在 Python 启动前通过 `RUNNER_TEMP` 写入 `GITHUB_ENV`；未改变隔离范围，后续 CI 以修复后的 SHA 为准。

第二次 `34211780588` 在 Windows pip 缓存探测和 Linux 平台记录步骤失败，测试尚未执行。Linux `platform.platform()` 的子进程查询被隔离探针拦截 1 次；移除非必要 pip cache，Python 平台信息改读 sys，操作系统信息由显式 shell 命令记录。隔离规则没有放宽。

- Linux 与 Windows CI 结果及最终 HEAD 必须在推送后独立核对；PR-context 检查未因分支 push 自动等同通过。
- 未运行真实研究目录、Final Test、真实执行器和原有真实工作区历史对账：`REAL_WORKSPACE_RUNTIME_INDEPENDENTLY_VERIFIED=NO`。
- 未认证网络文件系统、分布式主机锁、断电/磁盘损坏耐久性、POSIX fork 继承分支；已测试的是 subprocess 与进程强制终止。
- 继承的 12 项排除与 legacy module skip 不属于本轮通过数；不为凑数运行真实依赖测试。
- 没有开启预测执行、扩大人工权限、引入后台恢复、merge/auto-merge 或 P3-C。当前仅准备独立复核，不自认证整体 Phase 3 完成。

回滚：本轮提交后在独立分支执行 `git revert --no-edit <P3-B提交SHA>`；不 reset main、不回写原研究目录。代码回滚不自动删除 synthetic 或外部既有 journal；新 v2 journal 不能由旧代码假装已兼容，应保留证据并停止旧版写入。
