# R1 快照限定接入与合成回归

本轮将固定快照作为新的工程来源，完成限定 DAILY/RAW 子图接入。不是恢复历史版本、历史数值等价证明、真实数据认证或 Trial 授权。独立差异复核待完成，不合并、不启用 auto-merge、不开始 R2。

## 来源与改动边界

基线 HEAD `d433709b01289097f89eb24d19c9f147e38ca985`；远端 main `d3dcb68934ea8fb058c98039181d29894b6425df`；分支 `codex/r1-trusted-source-recovery-v1`。实际在独立部署目录工作，保留已有 progress.md 的快照交付追加段。

完整读取用户接入提示词、独立审查及 E01–E10 原文证据；配套 JSON 的静态说明不冒充项目测试。固定包 SHA256 `af9643db56a3218a40ad6b5f12fa50613f076e8f371f9f3b5641bffafb4aed58`；17/17 固定源码字节与原清单匹配。没有重新采集原工作区源码或搜索历史。

原始和适配后的身份见 [来源清单](R1_SNAPSHOT_INTEGRATION_SOURCE_MANIFEST.json)。区分本机 CRLF 原字节与部署 LF 文本哈希，冷进程另记录实际 checkout 的来源。原始两个脚本分别为 48,823 / 62,801 字节，SHA256 分别为 `fa7d5439e6f152e4ac6eceffbf29fab5d0d51a5fac3e487afc1542f4384063e1` / `3c9fe6ad2ce6558515a6612369ed03dc7f3c9cfc694b511059cce19dabbd86a1`。

只提取两个脚本必要函数，移除历史批次正文及无用导入。main/main_run、失效标记、旧 TrialRegistry、legacy.run/run_candidate/verify_freezes 均在 IO 前拒绝。原文仍在本地固定快照，未提交整个 17 文件包。当前 io_safety、编译器、退出评估器、费用、成交、预算、治理和统计政策保持原实现。

两个现有包文件有必要的来源元数据适配：引擎构造器可显式传递 source_identity 给既有 manifest builder，缺省保留原 Git 采集。限定 runner 传 UNKNOWN/dirty=True，不隐式启动 git；两个部署脚本的实际内容哈希另进入指标来源字段。没有改 EngineConfig 或其 hash、交易规则或 manifest 字段含义，不把 snapshot hash 伪装成 Git commit。数据、股票池和日历身份绑定传入内容，去掉历史硬编码身份。

## 项目 red/green 与失败保留

| 发现 | 修正前实际结果 | 最小修正及验证 |
|---|---|---|
| F01 | 空双来源对未出现证券返回 True | 每证券/日期要求双来源覆盖；缺单源、整文件、缺日、越界、UNKNOWN、冲突全部拒绝；完整正常通过 |
| F02 | 高分 A 不可交易时输出空，B 被提前丢弃 | 删除提前 head；复用同一编译器资格、排序、Top-N；已持仓、NaN/Inf、乱序、同分、复合百分位均验证 |
| F03 | 100 买入、103 到期、107 卖出，延迟为 0 | 固定持有使用绝对交易日历坐标；结构退出使用真实 EXIT_DECISION 触发索引；验证 0/100 原点、整体平移、延迟、部分成交与尾部待卖 |
| F04 | 压力收益已改变但 annualized_return 沿用 BASE | 显式限定可复用路径字段，重算年化、敞口、未实现盈亏；盈亏输出按现有账务快照四位小数；BASE=1、平仓/未平仓/部分成交均核对 |

第一轮 F01 已 red，其余三个用例在引擎构造 manifest 时被 git 进程隔离拒绝：3 次拒绝尝试，不能记为四项全部 red。明确来源传递适配后，四项均在真实部署组件上 red，再实施上述修正并 green。没有 mock 编译器、engine、PIT 或成本模块。

扩展验证保留两类施工失败：部分成交 fixture 最初未考虑开盘容量用前一交易日成交量，调整输入后才真正形成部分成交；导入清理曾误删 helper 的公开编译器绑定，完整回归出现 26 failed/80 passed，已恢复该真实绑定。没有删断言或降低门禁。后续 109 passed 的完整 R1 回归，以及最后新增零原点/来源清单输出后的 51 passed 定向与冷加载，分别记录，不相加为一次测试计数。

本地证据为 Python 3.13.5、独立 .venv、现有 hash 锁依赖；最终 51 用例中真实合成 engine.run=29，另有 2 个真实 Ledger/Fill 的纯指标原点用例。禁用执行器、网络、进程、保护路径探针均为 0；109 用例另有现有合成审批/确认各 59 次。首次 3 次 git 拒绝和最终零计数分开保留。

## 支持与阻断矩阵

| 合同或入口 | 本轮范围 |
|---|---|
| 正式 corrected loader、legacy helper | 从部署 scripts 冷加载；不加 sys.path，不采用 sys.modules 中同名脚本；实际 file/spec.origin 归属源码树 |
| 包依赖/资源 | 冷进程记录全部已加载 chanlun_trader 来源；原 17 入口、两个 loader、prompt、schema、config 验证；部署复制树、不同 cwd、数据根毒化、缺 corrected/helper/prompt 验证 |
| runner | 仅 DAILY/RAW、DAILY_SIGNAL、T_CLOSE、NEXT_SESSION_OPEN；其他频率/价格/时点明确拒绝 |
| policy/candidate | 仓位数、lot、现金覆盖必须一致；日历有序唯一且在政策和研究封存边界内；容量仅现有 0.10 合同 |
| 因子可用时间 | 输入必须显式提供 available_at，直接传给现有编译器/退出层；迟到输入不重新标成当天收盘；缺证据拒绝 |
| PIT | 仅已有逐日双来源正常化文件；不发明期间状态前填规则；不完整期间表示不能冒充逐日覆盖 |
| 退出 | FIXED_HOLD、STRUCTURE_INVALIDATION；不支持的退出类型拒绝；不改变真实触发和成交 |
| BASE/10K | 独立引擎和 lot 生命周期，10K 使用独立 policy 的现金；不复用 BASE 卖出流 |
| 输出 | 默认 write_evidence=False；显式开启必须提供绝对 evidence_root，与部署源码根及数据根分离；不从 trial_id 拼接隐式写入路径 |
| 基准 | 原 E 盘路径已去除；文件读取合同未接入。可选诊断 {} 对应 UNKNOWN；required/硬状态门禁缺证据明确拒绝 |
| 成本压力 | 固定 fill/order/lot 轨迹，重算费用、权益、收益、敞口及盈亏；未知 BASE 扩展字段不输出；路径统计复用不表示重新认证压力执行可行性 |
| bootstrap/subperiod/classification | 保留原纯函数定义、固定种子/窗口/局部判定；未运行完整统计研究。当前 BH/最终裁决服务未改，局部分类不授予最终资格 |
| 正式调用方 | 两个现有 loader 冷加载已认证。调用方缓存当前未传 available_at，不能假造；默认不再隐式输出。真实输入/输出合同适配未认证，不能启动正式 Trial |
| 未覆盖 | wheel 安装、事件/分钟执行、基准文件读取、真实因子生成身份、真实数据/公司行动、正式 Trial/PerformanceAccess、Paper、订单和全部深层延迟分支 |

SOURCE_LOADED 与 SYNTHETIC_GOLDEN_VALIDATED 只在上述范围成立；REAL_DATA_READY 与 TRIAL_AUTHORIZED 均不成立。既有数据 readiness 中 CORRECTED_EXECUTION_INPUT_PARITY 未验证标记继续保留。

## 验证命令与 CI

测试 import 前使用 `PYTHONPATH=tests/isolation;src`、`CHANLUN_TEST_ISOLATION=1`、`CHANLUN_PROTECTED_ROOT` 指向原研究工作区、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONUTF8=1`。本地运行 `.venv/Scripts/python.exe -m pytest -q -s tests/research_factory/test_r1_snapshot_integration.py tests/research_factory/test_r1_source_closure.py tests/research_factory/test_r1_data_readiness.py`；最后增量只重验受影响定向与冷加载。现有 R1 workflow 仅增加本轮测试文件，原五阶段选择器、隔离及锁依赖未改。

本地原始日志保留在忽略目录 tmp：r1-snapshot-red.log、r1-snapshot-red-after-source-adaptation.log、r1-snapshot-green-initial.log、r1-snapshot-matrix-first.log、r1-snapshot-matrix-second.log、r1-snapshot-all-r1.log、r1-snapshot-targeted-final.log、r1-integration-local-final.log、r1-targeted-final.log；JUnit 为 r1-integration-local.xml 和 r1-targeted-final.xml。不得删除前期失败或将旧 SHA CI 当新源码认证。

提交时 CI=PENDING；最终 HEAD、run/job、版本/文件系统、各阶段计数在 PR 和外部本地证据 tmp/r1-integration-ci.json 绑定，不为状态更新另造提交。没有 PR-context 认证不声称具备合并条件。

旧 cb31d9f Windows L1 事件继续 OPEN / ROOT_CAUSE_UNCONFIRMED。本次有明确 git 调用栈的受控拒绝不证明旧根因；没有无假设重跑、放宽白名单或新 skip/xfail。

```text
HISTORICAL_PROVENANCE=UNVERIFIED
REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED
READY_FOR_REAL_TRIAL=false
R1_FULLY_CLOSED=false
MAIN_MERGED=false
R2_STARTED=false
```

回滚本轮实现使用 `git revert --no-edit <本轮实现提交SHA>`；基线为 d433709。恢复缺源码阻断，不 reset main、不触碰原工作区或历史诊断。
