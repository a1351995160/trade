# B：隔离合成批次协议实施

本模块按用户独立批准 B 实施。A Objective 身份、新颖性比较集和计划/Paper 使用资格分别保留职责；它们不能相互替代。当前为 IMPLEMENTING，不是 R3 或总工程完成认证。

## 已接通的服务

请求必须列明已完成设计审批、冻结和物化的候选，逐项绑定各自正式 v2 Objective、execution_binding、冻结合同、预算和统计家族、新颖性确认、实际数据字段及文件身份。一个批次可列出多个正式目标的已知候选；每个目标沿用原创建事务得到的预算和单槽家族，不合并或扩张原额度。

预览包含候选数、Trial 数、原批次数、并发、重试、模型、调用/token/费用、时间、内存、有效期。首版只支持单 worker、零重试、无业务模型；缺失数值或不支持的配置拒绝。实际测试操作方通过 confirm 产生 SYNTHETIC_TEST_HUMAN 父批准，自动动作的记录为 BATCH_DELEGATED，引用父批准、候选和执行身份。

调用顺序为 `request → confirm → run/execute_next`。独立的 `pause/stop/revoke/resume` 控制不扩大任何范围；撤销/停止不能恢复。预留执行身份与并发检查在原共享互斥内完成；创建实际受限子进程后记录 launcher，worker 核验父进程关系后注册。启动事件是动作开始的线性化点，长期计算不占用批次锁，首次性能准入在原新颖性边界内再次核验父批准及全部绑定，然后使用原 TrialLedger 标记性能访问。

原 v1 启动入口拒绝消费批次委托。明确版本的委托适配保留原 Structural、预测许可、冻结合同、预算、Trial、裁决及 registry 检查。旧 CP 的 PHASE2_PREDICTIVE_EXECUTION_DISABLED 未修改。

Objective 创建回执及不可变家族必须匹配原创建事务中的实际工件摘要；不能只凭预览内容 hash 另写家族。委托适配拒绝收尾类型变更和重试入口。删除候选 metadata 也不能绕过 canonical 启动意图中的批次标记。来源共享锁保持至正式 caller 取得内存输入快照；两个 engine 在锁外运行，既不允许读前替换，也不把整个回测装入批次锁。

正常完成以原领域事实结算。worker 异常退出后，使用原失败协议释放尚未消费的合法预留，或消费已经访问性能的槽位并保留工程中断。批次零重试，不自动重新执行。已退出控制者的 `recover` 只恢复审计和结算；不能以 completed receipt 取得新启动权限。

## 当前验证

- `batch-contract.log/XML`：13 项合同初验通过，实际正式创建/审批/冻结/物化/新颖性确认作为前置。
- `batch-execution.log/XML`：14 项通过，单候选实际 Structural、Trial、engine、原预算及重放。
- `batch-settlement.log/XML`：19 项通过，包含原完整 R2 正常及中断恢复、OS 资源测试与新批次合同。
- `batch-multiple.log/XML`：两个事前明确设计、分别正式创建/审批的候选，一次父确认后四个动作完成；每个原预算 used=1/reserved=0，委托均非 LOCAL_HUMAN。
- `batch-interrupted.log/XML`：实际性能准入后、engine 方法体执行前 worker 退出73；原消费1、预留0，批次 BLOCKED、无自动重跑。
- `batch-controls.log/XML`：实际创建进程后、准入前撤销阻断；并发第二次预留拒绝，停止后不能创建新许可。
- `batch-controller.log/XML`：控制者预留后实际退出73，新进程恢复为 BLOCKED，再次恢复不产生执行或额度变化；实际确认后到期不能预留。
- `batch-stage.log/XML`：77 passed / 22 failed。22 项旧 `test_predictive_trial_start_v1.py` 在 fixture 第40行依赖缺失历史合同，尚未进入被测启动服务；该测试文件与 e288746 相同。保留全部失败，不复制真实研究合同、不添加 skip、不修改旧测试或隔离白名单。后续独立合成集合通过不能将这22项改记为通过。
- `batch-synthetic-stage.log/XML`：78 passed / 1 failed。负向测试假设96 MiB必然无法执行领域服务，但实际 Structural 成功；这是测试输入假设错误，不是资源执行失效的red。
- `batch-recovery-final.log/XML`：4 passed，负向配置明确为8 MiB后实际阻断；同时验证控制者退出、到期及撤销事件写入后/head写入前退出73。恢复只前滚一个已经完整落盘的相邻事件，不回退历史，不以较旧批准继续。
- `batch-core-final.log/XML`：24 passed，覆盖当阶段新批次、OS资源及原完整R2链，隔离三项探针0。
- `batch-admission-review.log/XML`：27 passed，含创建事务/家族证据、禁止重试、跨域、完整批次及恢复；另一真实操作方在性能前撤销或改来源时，engine0/performance0，原预留按协议释放。
- `batch-snapshot-boundary.log/XML`：5 passed，包含上述两类竞争、快照读取期间实际共享锁拒绝替换，以及两个原R2完整服务场景。快照场景engine2/performance1；长期engine不在批次锁内。
- `batch-root-check.log/XML`：2 passed，原始绝对工作区先验证再规范化；相对路径不能悄悄变为获准输入根。
- `batch-quota-final.log/XML`：2 passed，显式Trial上限不足拒绝；实际消费后申请另一批次仍被原家族预算拒绝，不重置额度。

原始日志均在 `E:/llmwiki/roadmap-engineering-evidence`，新测试将每个临时根的原始流分目录保存。以上是阶段本地证据，不代替最终固定 HEAD 认证。

## 必要剩余验收

仍需版本化操作入口及统一界面接入和相应真实使用流程；固定新 HEAD 的全路线回归、双平台及新版集中审计。不能把这些登记项当作已经交付。旧历史 fixture 的22项失败保持独立记录，兼容性还需既有独立合成 P3 套件验证。

真实数据 NOT_VERIFIED，READY_FOR_REAL_TRIAL=false，R1_FULLY_CLOSED=false。无真实合格策略、真实观察0；合成最终 BLOCKED/REJECTED 可以是工程链正确完成的结果，不要求 RESEARCH_PASSED。所有既有 OPEN 事件及旧集中审计包保持不变。

## 阶段平台差异（320e980）
PR R1 run34490995583的Ubuntu通过，Windows在新批次夹具准备阶段报NOVELTY_SOURCE_MISSING_OR_LINKED：376 passed、9 failed、20 errors；push run34490990216被取消，不能记通过。原件与附件在batch-ci-pr-34490995583。测试父进程原来直接使用tempfile临时路径，实际创建子进程使用resolve后的路径；现于创建前统一规范路径并保存二者诊断。非规范临时别名的真实服务本地回归通过，新Windows认证尚待验证。服务来源校验、超时、skip和隔离规则未放宽，L1/L6继续OPEN。

## 正式操作入口与页面
`create_app(explicit_synthetic_root, ExecutionPolicy("GOVERNED", "SYNTHETIC"), engineering_workbench=...)` 装配现有应用，启动前必须设置 CHANLUN_TEST_ISOLATION=1 和 CHANLUN_PROTECTED_ROOT；根路径不由 HTTP 请求提供。只监听本机。默认 READ_ONLY 仅可查询历史；旧 CP 预测入口保持原禁令。

页面 `/research/workbench` 增加有界批次区域，和既有计划/Paper对账共用显式根。候选来自已声明的全部比较来源，服务逐个复核绑定；来源异常、缺确认和原预算耗尽均显示阻断原因。用户选择已知候选、动作和明确数值，生成预览后核对完整来源/政策/家族/数据绑定，通过合成测试人工确认才能运行。批次动作完成不表示 RESEARCH_PASSED。轮询只查询状态；暂停/停止/撤销仍可在执行请求未返回时操作。连接中断不表示后台停止，恢复只做审计与原结算。

接口前缀 `/api/research-engineering/batches`：GET `/context` 与 `/{id}`；POST `/request`、`/{id}/confirm`、`/{id}/run`、`/{id}/pause|resume|stop|revoke|recover`。confirm要求confirmed、test_confirmation及原preview_hash；控制要求confirmed和test_confirmation。只读GET不恢复事件、不写批准。未来生效的批准也可暂停或撤销，到期批准可撤销，不可恢复终止批准。

本机真实页面验收驱动（仅工程测试依赖，不是生产研究入口）：
```powershell
$env:PYTHONPATH='tests/isolation;src'
$env:CHANLUN_TEST_ISOLATION='1'
$env:CHANLUN_PROTECTED_ROOT='E:\llmwiki\chanlun-trading-system'
$env:CHANLUN_PROCESS_EVIDENCE_DIR='E:\llmwiki\roadmap-engineering-evidence\batch-ui-process'
.venv\Scripts\python.exe tests/research_factory/synthetic_batch_demo.py --port 8789
```
每次生成全新隔离临时根，目标、设计、冻结、物化、新颖性前置均由真实服务形成；批次批准与计划/Paper资格不自动生成。访问页面选择两个候选时，候选/Trial/原批次数都必须明确设为2。墙钟120秒、worker2048MiB、并发1、重试0及业务模型费用0是本次明确测试配置，不是无限默认额度。服务只支持文档资源模块可实际强制的配置。停止示例服务器不会删除历史，不得重启旧回执免费重跑。

`batch-web-final.log/XML`：4项真实API测试通过，包括实际两动作执行、未确认拒绝、只读不写、非本机拒绝、未来/到期撤销、旧CP拒绝。`batch-ui-build.log`构建通过；保留现有bundle体积警告，未调大阈值。实际浏览器批次625e3ba4e2244d4abdbb556385a7d868一次确认后4/4动作完成，四项均BATCH_DELEGATED；页面显示原预算耗尽、VALIDATION_BLOCKED、合格策略0、真实观察0，浏览器error/warn为空。原HTTP状态分别保存在batch-ui-completed.json、batch-ui-context.json、batch-ui-workbench.json，服务器日志batch-ui-server.log。页面导出能力不可用，未伪造导出文件。

操作入口及页面已完成上述本地验收；最终整体固定HEAD与双平台认证仍待完成。本节覆盖前面的“必要剩余验收”中入口/界面开发状态，但不覆盖最终交付要求。

## 进程传输收紧（Sonar S6350）
abb0073的Sonar安全门禁C，规则pythonsecurity:S6350指出HTTP批次标识经服务传入通用Popen参数。已有标识检查且shell=False，未证明存在可利用命令注入，也未标记误报或修改规则。生产启动命令现固定为当前Python及chanlun_trader.synthetic_batch_worker；根、父批次和执行身份经原资源握手的JSON标准输入传入，worker严格检查数据字段，再执行原父批准/进程关系/动作核验。数据通道不能产生权限，原预览/确认/预算/历史记录格式不变。

batch-stdin.log/XML：18项定向测试通过、22项未选（只表示此次受影响选择范围，不是最终全套）；含真实API两次固定命令启动、6类无效上下文拒绝、真实OS限制、两个候选连续执行、性能后真实退出和三类并发边界。worker-launch-transport.json保存实际命令与数据身份，不替换runner或权限。最终新HEAD仍需全部套件和Sonar验证。
