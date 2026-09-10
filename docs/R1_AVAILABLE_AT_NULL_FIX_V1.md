# R1 available_at 空值修正与原锁依赖恢复

本轮复核基线为 `f4a3681ccf8d44c71bcfa71bf04a0ea2134a5539`，复用 PR #7，保持 Draft。只修复限定 DAILY/RAW 合成 runner 的因子可用时间缺口；原 F01–F04、治理权限、固定策略参数及来源边界不变。

## 依赖恢复事实

- 官方版本 JSON 与实际下载确认 `annotated-doc==0.0.5` 存在，要求 Python >=3.9。wheel 为 5302 bytes，SHA256 为 `117bac03a25ede5df5440e855b32d556049ca169ead221505badf432fed4b101`，与原锁一致。
- 此前官方源命令错误地使用 `--index-url https://pypi.org`，遗漏 `/simple`；对应 `/annotated-doc/` 本轮 HTTP 404，正确 `/simple/annotated-doc/` 与官方 wheel 均 HTTP 200。不能从旧 `No matching distribution` 推断发行版不存在。默认清华镜像的 TLS EOF 是另一个未确认根因的问题。
- 本机 Python 3.13.5、pip 25.1.1；沿用已有本机代理与证书链，只在安装命令指定正确索引。没有更改全局网络、关闭 TLS、升级解释器或 pip。
- 新建 `.venv-r1-locked` 后按原 `requirements-p3b.txt` 及递归原锁 `--require-hashes` 安装全部依赖，`pip check` 返回 `No broken requirements found`。锁文件字节哈希未变。安装包含 pip 缓存分发文件，pytdx 使用 pip 接受的缓存构建 wheel；不声称完成全套源码构建的独立可复现认证，也未把构建 wheel 哈希写入原锁。
- 下载探针的 `--no-deps/--only-binary` 仅用于单包，没有用于完整安装。未复制其他环境 site-packages、未挂接旧 editable 安装。

## 项目级 red 与证据分层

恢复 `tests/isolation`、保护原研究目录、禁用执行器/业务网络/进程探针后，先运行原隔离先验：66 passed；原 loader/runner 正向与未来时间、缺列测试：2 passed，真实合成 engine 2 次。

随后通过原 `inputs`、真实 `load_corrected_module` 和 `run_corrected_candidate`，仅更改可用时间单元格。入场复现保持候选、政策、价格、证券状态、排名均不变。已持仓用例复用原结构退出合成方式，在入场后第 2 个 session 起置空因子时间。

- 13 个入场用例真实放行：object 列的 None/NaT/NaN/空字符串/字符串 NaT，分别混合单行及全部缺失；原生时间列另有 None/NaT/NaN 三项。均实际产生 BUY，混合输入错误选择高分非法行。
- 5 个结构退出用例真实使用未知时间因子，产生 `EXIT_DUE_STRUCTURE_INVALIDATION`。
- 本机 pandas 3.0.5，原生列实际为 `datetime64[us, Asia/Shanghai]`；None 与 NaN 赋值后实际送入值均为 `NaTType: NaT`。object 列另记录每格真实 repr 和类型，不把赋值意图当作实际 dtype。
- 不可解析文本 `not-a-date` 被原 `build_day -> ensure_aware -> pd.Timestamp` 的 `DateParseError` 阻断；不计作放行 red。
- 首轮 34 项为 28 failed / 6 passed、engine 27 次。其中 7 个固定持有测试误设 OR 合同，被真实 compiler 拒绝，属于测试装配错误，不是业务 red。保持生产校验，改回原合法固定持有 fixture 后单独补跑：6 failed / 1 passed、engine 7 次；5 项仅缺新增诊断字段，1 项为原有解析阻断。这些失败不计为固定持有业务缺陷。

## 最小改动与可见行为

在适配器 `build_day` 的共享因子行边界检查原始缺失值、沿用 `ensure_aware` 解析后的 NaT，以及解析异常。非法行同时不进入入场 rows 和持仓 factor_state，`metrics.signal_diagnostics.INVALID_FACTOR_AVAILABLE_AT` 按拒绝行计数；全部非法时返回零信号并有明确诊断，混合输入继续使用合法行。

未改共享 compiler 或全局时间函数。独立直接调用旧 compiler 不在本轮认证范围内；本轮认证的是部署 loader 到限定 runner/engine 的路径。

没有补造时间、前填、回填或改写输入。合法未来时间继续交原 compiler/退出 evaluator 处理，包括原未来因子会延后固定持有退出的既有行为。非法行被移除后，固定持有按原日历运行，结构合同的既有固定持有兜底也保持；不允许未知时间因子贡献结构退出。合法 naive 与 aware 时间仍使用原时区规则。

## 回归与认证

定向完整集成文件：81 passed、engine 63 次，包含所有原测试及新增 34 项。对修正前后五个合法时间对照逐项比较完整信号、订单摘要及退出决定，全部相同；空值固定持有的首次退出决定也相同。非法时间不再造成后续重新入场，所以不声称非法输入下全部后续交易轨迹相同。

本机完整 R1：146 passed（146.65s），engine 63 次，合成审批/确认各 59 次；原 F01–F04、源根毒化、真实 loader 正向及缺依赖负向全部保留通过。Python 3.13.5，源码工作区 E: 为 exFAT，临时 fixture 所在 C: 为 NTFS；不冒充旧 CI 的 Python patch 或文件系统。

新增用例逐次记录真实 engine 次数、禁用执行器/网络/进程/保护目录计数、运行期间写打开次数；逐次比较 DataFrame 和合成只读文件哈希。定向运行这些禁止计数及写打开次数为 0。

完整 R1 及最终 HEAD 的原五阶段、双平台分支/PR-context CI 使用真实新结果，原日志不覆盖。提交时远端状态为 PENDING；最终 run_id、event、head_sha、checkout_sha、Python/文件系统、Sonar 和 required checks 留在独立交付证据及 PR，不为状态反复提交。

依赖及 red/green 原始脱敏日志、逐例 dtype/值/计数、比较结果位于独立交付目录 `E:/llmwiki/r1-dependency-evidence`。源码工作区为 `E:/llmwiki/r1-available-at-null-fix`；未读取原研究数据。

## 保留边界

`HISTORICAL_PROVENANCE=UNVERIFIED`；`HISTORICAL_WINDOWS_L1_INCIDENT=OPEN_ROOT_CAUSE_UNCONFIRMED`；`REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED`；`READY_FOR_REAL_TRIAL=false`；`R1_FULLY_CLOSED=false`。未 merge、auto-merge、退出 Draft 或开始 R2，未扩大进程白名单。

回滚本轮修正可使用 `git revert <本轮修正提交SHA>`，原基线为 `f4a3681ccf8d44c71bcfa71bf04a0ea2134a5539`。历史日志保留，撤销另行追加记录。
