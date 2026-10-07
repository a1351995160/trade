# PR 36 SonarCloud 安全告警审阅记录

## 2026-10-07：899dcbc源码的实际后续结果

以下原2026-10-03记录作为历史取证保留；本节给出后续已核实状态。

- 源码提交899dcbc85b1f524681bbb18761c17162ff5a52d6的公共流程、正式范围、全市场和长期四组Windows/Linux矩阵共8项全部通过，PR事件的10个工作流全部成功。证据为同目录CI_COMPLETION_899dcbc.json，官方run为37170907404，最后一项于2026-10-04T03:39:59Z结束；原旧失败job仍为失败，不改写其状态。
- SonarCloud同一源码的官方清单为11项OPEN vulnerability，与前一源码fd3211b的11个key一致，没有新增。npm安装脚本项已通过--ignore-scripts定点修复，不在当前清单中；旧12项表仍仅描述39268aa历史分析。
- SonarCloud质量门禁及GitHub检查仍失败，失败条件是new_security_rating。其余11项的具体本地入口、固定参数数组、冻结名称/段链及根目录边界人工证据仍适用；服务端未接受或关闭，不称为已关闭安全问题，也没有执行误报转换、抑制或门禁变更。当前官方审阅快照为同目录SONAR_CURRENT_HEAD_REVIEW_899dcbc.json。
- 本节只证明899dcbc源码的CI和静态扫描状态，不证明六账户真实验收完成或策略有效。最终交付元数据提交仍须在其实际HEAD上完成必需CI，不能继承旧提交绿色状态。


记录日期：2026-10-03。审阅对象：`a1351995160/trade`，PR `36`，SonarCloud 分析提交 `39268aa5fd5359620845da1d3276a0cf5a24cde1`，分析时间 `2026-10-03T22:37:10+08:00`。

**当前状态：人工证据审阅完成；外部 SonarCloud 状态仍为 FAILED（API 为 ERROR）；未取得已登录 SonarCloud 会话或 Administer Issues 授权。** 本记录未将任何告警转换为误报，未批准安全门禁，未变更 SonarCloud API 状态、规则或质量门禁，未添加 `NOSONAR` 或批量排除。

本记录区分扫描结果、逐项调用链证据和本地修复验证。下文告警行号均对应上述扫描提交；后续工作区修改可能使行号移动。

## 已确认的扫描与 CI 结果

SonarCloud 官方 API 返回 12 个未解决的 vulnerabilities、217 个 code smells。质量门禁仅 `new_security_rating` 失败：实际值 `5`，阈值 `1`，比较条件 `GT`。其余返回条件均为 OK：`new_reliability_rating=1`、`new_maintainability_rating=1`、`new_duplicated_lines_density=0.2`、`new_security_hotspots_reviewed=100.0`。

Windows job `111223728278`，run `37130234832`，名称 `completion (windows-latest, long-horizon)`，已于 `2026-10-03T14:44:39Z` 完成并失败。Python 为 `3.13.15`。测试结果为 **13 failed、235 passed**；13 项失败均在 `tests/research_factory/test_universe_evidence_v2.py`，共同提前触发 `UNIVERSE_AUDIT_OWN_STATE_PATH_REDIRECTED`。首个失败调用链为测试第 49 行 → `universe_evidence_v1.py:958` → `universe_evidence_v2.py:544/535` → 第 406 行的路径检查。三项原本应拒绝篡改的测试因提前触发该检查而出现错误码正则不匹配。

日志公开的 `runner.temp` 为 `D:\a\_temp`，pytest 使用 `--basetemp=D:\a\_temp/long-horizon-tests`。日志未打印 `TEMP/TMP`、`tempfile.gettempdir()` 或具体 `universe_own_audit_` 路径，因此本记录不推断该临时目录的实际别名目标。

主协调代理已在工作区将程序自己创建的 `TemporaryDirectory` 根目录先 `.resolve()`，再交给既有审计检查（当前 `universe_evidence_v2.py:543–544`）；生产路径重定向检查保留。主协调代理提供的本地相关测试结果为 **16 passed**。该修复的远端 Windows 验证仍待后续提交对应的 CI 结果；旧 job 的失败状态不会因本地测试通过而改变。

## 12 项告警清单

| 告警 key | 规则 / 严重级别 | 扫描提交中的位置 | 本次处理或人工审阅分组 |
| --- | --- | --- | --- |
| `AaECNOzB2ukvKcrcbJhZ` | `githubactions:S6505` / MAJOR | `.github/workflows/autonomous-completion.yml:132` | A：安装生命周期脚本；工作区已修复 |
| `AaECNOtm2ukvKcrcbJgp` | `pythonsecurity:S6350` / MAJOR | `scripts/run_long_horizon_universe_acceptance_v1.py:221` | B：固定公共 CLI 子进程参数 |
| `AaECNOtm2ukvKcrcbJgr` | `pythonsecurity:S8705` / MAJOR | 同上，第 221 行 | B：维护者 CLI 参数被当作 HTTP 来源 |
| `AaECNOtm2ukvKcrcbJgq` | `pythonsecurity:S6549` / MAJOR | 同上，第 229 行 | C：冻结任务 checkpoint 存在性分支 |
| `AaECNOxo2ukvKcrcbJhY` | `pythonsecurity:S8707` / MAJOR | `scripts/run_strategy_account_v1.py:29` | D：本地明确指定的 job 文件哈希 |
| `AaECNOxo2ukvKcrcbJhX` | `pythonsecurity:S6549` / MAJOR | 同上，第 380 行 | E：受限 worker 的 checkpoint 续跑分支 |
| `AaECNOxo2ukvKcrcbJhU` | `pythonsecurity:S6549` / MAJOR | 同上，第 383 行 | E：同一 checkpoint 的哈希分支 |
| `AaECNOxo2ukvKcrcbJhW` | `pythonsecurity:S6549` / MAJOR | 同上，第 422 行 | F：任务根目录身份检查 |
| `AaECNOxo2ukvKcrcbJhV` | `pythonsecurity:S6549` / MAJOR | 同上，第 933 行 | E：本地任务状态读取分支 |
| `AaECNOja2ukvKcrcbJgG` | `pythonsecurity:S8707` / MAJOR | `src/chanlun_trader/research_factory/research_evidence_v1.py:33` | D：独立核验入口读取本地 job |
| `AaECNOja2ukvKcrcbJgF` | `pythonsecurity:S2083` / BLOCKER | 同上，第 37 行 | G：已校验名称、段号和根目录的资源原件哈希 |
| `AaECNOg12ukvKcrcbJgA` | `pythonsecurity:S6549` / MAJOR | `src/chanlun_trader/research_factory/universe_report_state_v1.py:57` | F：制品根目录重定向检查 |

### A：npm 安装生命周期脚本

第 132 行的 `npm ci` 缺少 `--ignore-scripts`，该告警指出了可直接收紧的安装行为。主协调代理已将工作区流程改为 `npm ci --ignore-scripts`（当前第 133 行），并提供前端 Node 测试 **27 passed**、生产构建通过的验证结果。本审阅代理未重复安装或测试。该工作区修复尚不能代表 SonarCloud 旧提交中的告警已经关闭。

### B：公共 CLI 子进程的两项参数注入报告

`S6350` 流为 `run_acceptance:296/303` → `_public_execute:204` → `Popen:221–222`。`S8705` 另从第 345 行 `argparse.parse_args()` 经第 348–349 行进入同一调用链。官方流文本将此处来源标为可构造恶意 HTTP 请求，但第 345 行实际读取本地维护者 CLI 参数。

调用使用固定 `sys.executable`、固定仓库脚本 `scripts/run_trusted_research_v1.py` 和由代码选择的 `start/resume` 操作；参数以数组传入，未启用 shell。`task_id` 在第 295 行由 `stable_hash` 生成，任务由服务查询或冻结后传入。`workspace` 与 `deployment` 为维护者选定的部署参数；`scripts/lifecycle_deployment_v2.py:24–28` 明确该部署配置不能由 HTTP 请求提供，第 37–41 行检查工作区根目录与配置结构。

这两项报告未展示 HTTP 用户控制命令、脚本或拼接 shell 内容的路径。当前调用上下文具备具体误报论证；参数数组本身并不代替所有输入验证，后续若将这些维护者参数暴露为外部请求输入，应重新审阅。可以定点规范为完整路径和 `--key=value` 参数形式，但不需要删除真实公共 CLI 执行路径来绕过扫描。

### C：验收协调器的 checkpoint 存在性分支

`S6549` 流为 `_public_execute:206` 读取任务的 job → 第 228 行取已冻结 `backend_options.checkpoint_path` → 第 229 行 `checkpoint.exists()`。任务由 `run_acceptance:295–303` 的服务查询或冻结生成，该分支决定是否已到达安全暂停阈值。它没有独立接收 HTTP 请求中的任意文件路径。

本调用链的路径来源是部署服务生成的本地任务配置，具备误报论证。证据依赖维护者控制部署和任务目录的既有边界；本记录不把内容哈希当作外部身份认证，也不建议删除用于续跑协调的存在性检查。

### D：本地明确选择的 job 文件读取与哈希

`run_strategy_account_v1.py` 的 `S8707` 流为第 980 行 CLI `--job` → 第 989 行 `status(args.job)` → `status:909` → 第 958 行 `sha(path)` → 第 29 行打开同一个 job 文件。此路径是本地操作者明确要求读取的文件，报告中的 HTTP 来源标签没有对应实际 HTTP 处理器。

`research_evidence_v1.py` 的 `S8707` 流为同一 CLI 第 980/988 行 → `long_horizon_compute_worker:538/543` → `_long_horizon_compute_worker:550/585` → `verify_job_evidence:376/380` → `_json:33`。计算 worker 在第 556 行核对 job 与计算 scope 的哈希，并在第 562–566 行验证宿主握手、派发段和资源边界后进入独立核验。独立核验第 378–381 行再次将 `job.root` 绑定到所选 job 文件的父目录。

两项报告均针对内部本地 job 读取接口，当前证据未展示外部请求可以直接替换 CLI 的 job 路径。人工结论是可提交具体误报解释，未在 SonarCloud 中转换状态。

### E：worker 续跑与任务状态的存在性分支

第 380/383 行两项 `S6549` 的流为第 327 行加载 job → 第 336–337 行冻结 runtime 与 backend → 第 379 行取 checkpoint → 第 380 行存在性读取或第 383 行存在性哈希。此前第 327 行调用 `validate_sources`，第 330–335 行核对派发段、宿主握手和激活执行权限，第 338–339 行要求重新准备的计划与冻结计划一致。两个分支位于 `SegmentBoundary` 后的续跑回执处理。

第 933 行 `S6549` 的流为 `status:911` 加载本地 job → 第 932 行取得其 checkpoint → 第 933 行检查存在性，再读取处理日期。这个只读 CLI 状态入口依赖维护者提供本地 job，不能仅用 worker 的握手证据替它证明授权；其误报解释依据是实际入口和路径来源，而非该函数自身具有 worker 握手。

这些告警没有展示独立 HTTP 任意路径来源。若需要进一步收紧路径边界，应在任务加载处绑定或重建预期 checkpoint 路径，而不是删除 `exists()`、跳过内容核验或压制规则。本次没有作此类生产代码扩展。

### F：根目录检查本身被识别为文件系统 oracle

`run_strategy_account_v1.py:421–423` 将 `job.root.resolve()` 与所选 job 文件的规范父目录比较；不一致时拒绝 `JOB_ROOT_IDENTITY_CONFLICT`。该检查用于阻止任务根目录替换，删除它会削弱现有边界。

`universe_report_state_v1.py:53–64` 先构造 `ArtifactSequence` 校验制品清单，再于第 57–58 行拒绝根目录重定向，第 61–62 行要求各制品文件的解析父目录和名称与清单一致，第 63–64 行核对真实字节。官方流来自本地执行结果与报告调用：`run_strategy_account_v1.py:612/616` → `universe_research_report_v2.py:340/388–389` → `universe_report_state_v1.py:75/96/53/57`；未展示 HTTP 用户直接注入该路径的入口。

这两项 oracle 报告命中的是实际防护检查，具备按具体本地调用上下文解释误报的依据，检查保留。

### G：BLOCKER 资源原件路径遍历报告

官方流为 `research_evidence_v1.py:380` 读取 job → 第 381/416 行 → `_verify_segment_resources:327/330–332` → `etf_account_governance_v1.py:273` 读取 dispatch → 第 305 行返回段链 → `research_evidence_v1.py:343–345` 构造资源路径 → 第 353 行 `_sha(path)` → 第 37 行打开文件。

打开前已有三个具体约束：

1. `verify_job_evidence:378–381` 将任务根目录绑定到 job 文件的规范父目录。
2. `StrategyBatchGovernanceV1.__init__:135–137` 对所有策略名称执行 `re.fullmatch(r'[A-Za-z0-9_-]+', name)`，并核对策略身份及计划哈希；名称不能携带路径分隔符或 `..`。
3. `segment_status:273–282` 将 `dispatch.segment_number` 与从 1 递增的预期段号比较，并核对前序 head、profile、receipt、kind、dispatch 身份和段边界。带路径分隔符或 `..` 的文本不能通过该比较；随后第 344–345 行只拼接已经校验的名称、段号和固定 `_RESOURCE.json` 后缀。

因此，这条具体流并非把未经约束的路径字符串直接交给 `_sha`。扫描流未体现跨函数的名称和段链校验，人工证据支持针对该 BLOCKER 提交误报说明。该结论限于所列调用链，未泛化为所有 `Path.open()` 都安全，也未声称 SonarCloud 已接受解释。

## 官方证据与本地原始记录

以下均为直接的官方 API 或原始 CI 页面，不依赖第三方摘要：

- [PR 36 SonarCloud 页面](https://sonarcloud.io/dashboard?id=a1351995160_trade&pullRequest=36)
- [质量门禁 API](https://sonarcloud.io/api/qualitygates/project_status?projectKey=a1351995160_trade&pullRequest=36)
- [12 项未解决 vulnerability 及完整 flows API](https://sonarcloud.io/api/issues/search?componentKeys=a1351995160_trade&pullRequest=36&resolved=false&types=VULNERABILITY&ps=100)
- [PR 分析提交与时间 API](https://sonarcloud.io/api/project_pull_requests/list?project=a1351995160_trade)
- [SonarCloud API 说明](https://sonarcloud.io/api/webservices/list)：`api/issues/do_transition` 明确误报转换需要认证、项目 Browse 权限及 Administer Issues 权限。
- [当前 API 会话 API](https://sonarcloud.io/api/users/current)：本次未认证调用返回 `isLoggedIn=false`。
- [Windows 失败 job](https://github.com/a1351995160/trade/actions/runs/37130234832/job/111223728278)
- [GitHub job 元数据 API](https://api.github.com/repos/a1351995160/trade/actions/jobs/111223728278)

原始证据保存在 `C:/Users/84219/AppData/Local/Temp/long_horizon_release_orchestration_v1/`，文件名如下；TEMP 保存位置可能在系统清理后失效，本报告已记录关键状态、全部告警身份及实际调用链：

- `pr36_39268aa_sonar_quality_gate_diagnostic.json`：质量门禁原始 JSON。
- `pr36_39268aa_sonar_vulnerabilities_diagnostic.json`：12 项漏洞报告及完整 flows 原始 JSON。
- `pr36_39268aa_sonar_full_taint_flows_diagnostic.txt`：全部来源、传播、sink 行的可读导出。
- `pr36_39268aa_sonar_issues_diagnostic.json`：未解决告警查询第一页的原始 JSON（100 项；总数 229），不是全部 229 项的导出。
- `pr36_39268aa_job111223728278_diagnostic.log`：Windows job 完整日志。

本次只读权限核查未发现名称匹配 SONAR/SONARCLOUD 的本地环境变量；`gh secret list --repo a1351995160/trade` 未返回仓库 secret 名称。未读取或输出凭据值。未认证 API 状态不能代表用户浏览器中其他会话的登录状态，也不能证明用户账号没有相应权限；当前执行环境没有可用于误报转换的已认证权限证据。

## 尚未完成的外部状态

剩余 11 项的人工证据审阅已完成，其具体误报论证如上；外部 SonarCloud 安全门禁仍失败。后续需要拥有 Administer Issues 权限的已认证审阅者逐项确认并处理，或对明确发现的真实边界缺口作定点修复后重新分析。本报告未进行上述外部写入。

本地临时目录修复的 16 项测试、前端 27 项 Node 测试和生产构建通过，仅证明各自本地验证范围。修复后的远端 Windows long-horizon CI 与 SonarCloud 结果仍待对应提交的实际检查，不能据此称全部 CI 为绿色或安全门禁已通过。主用户操作指南未因本工程审阅附件而改变。
