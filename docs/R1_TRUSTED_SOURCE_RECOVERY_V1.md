# R1 可信源码恢复调查 V1

## 结论

本轮已完成获准范围的来源调查，未找到可恢复的 corrected Git blob。正式闭包保持 `BLOCKED_MISSING_SOURCE`，不是恢复成功。没有改动生产源码、测试、锁定依赖或 CI，没有引入替身或覆盖已认证模块。

附件已完整读取；附件中的任务建议按用户本轮明确请求执行，历史文档中的研究命令不构成执行授权。完整读取根 CLAUDE.md、指定三份文档及 progress.md；发布 checkout 中未发现 AGENTS.md/DEVAGENT.md，另遵守会话给定 AGENTS 规范。已阅读两处调用方、source_dependencies、R1 测试/冷 worker、import 前隔离设施及 R1 workflow。

## 基线与隔离目录

- 发布仓库：`https://github.com/a1351995160/trade.git`。
- 实测 `origin/main`：`d3dcb68934ea8fb058c98039181d29894b6425df`，与用户预期一致。
- 父提交：`1f6f29c7ad3e8d9371168dfa3bd5201723b48abd`、`404561c8867fdb27b01879ce553bac5e9079428f`。
- GitHub PR #6 查询确认 MERGED，mergeCommit 与上述基线一致，headRefOid 与最终认证 R1 部分交付 HEAD 一致；mergedAt=`2026-09-09T06:28:54Z`。
- 独立开发目录：`E:/llmwiki/trade-r1-trusted-source-recovery-v1`，从远端 main 全新克隆后创建 `codex/r1-trusted-source-recovery-v1`，初始干净。
- 使用独立克隆而非在原项目中创建 worktree，以免改变只读原项目的引用或 worktree 元数据；未在旧 R1 分支累加。

## 限定来源证据

原项目标识为授权路径 `E:/llmwiki/chanlun-trading-system`。`rev-parse --show-toplevel` 返回该路径；其当前 origin 也是发布地址，这个 remote 不足以证明目录完整保留了历史原项目。固定导出提交确实存在且类型为 commit；仓库不是 shallow。

以下只读命令均显式指定原项目路径，退出码为 0。空结果只针对精确路径，不扩展为“全部磁盘不存在”。

| 检查 | 实测结果 |
|---|---|
| `git -C <original> cat-file -t 42fbc52a2cc10212288a293e15c630ed09ea59f5` | `commit` |
| `git -C <original> ls-tree 42fbc52a2cc10212288a293e15c630ed09ea59f5 -- scripts/run_engine_corrected_phase4_v3.py` | 空，无该 tree entry |
| `git -C <original> log --all --format='%H %s' -- scripts/run_engine_corrected_phase4_v3.py` | 空，无本地 refs 可达路径历史 |
| `git -C <original> log 42fbc52a2cc10212288a293e15c630ed09ea59f5 --format='%H %s' -- scripts/run_engine_corrected_phase4_v3.py` | 空，显式检查固定提交祖先，避免该提交不在 refs 中的遗漏 |
| `git -C <original> rev-parse --is-shallow-repository` | `false` |
| 固定提交精确规范路径 AGENTS.md / CLAUDE.md / DEVAGENT.md | 仅 CLAUDE.md tree entry，blob=`4e29751d3414baeb85aeaa3649d2a64ff43151f6`；未恢复该文件 |

初次尝试读取根 AGENTS.md 返回不存在；没有对原项目执行 status、目录扫描、未跟踪文件读取、fetch、checkout 或写操作。没有重新搜索已确认无结果的发布历史。没有读取任何真实行情、数据库、合同、回执、运行态、绩效、Final Test、缓存、凭据或任意备份。没有运行/import 原工作区代码。

`ORIGINAL_REPO_ACCESS=SCOPED_SOURCE_AND_GIT_READ_ONLY`。本轮源调查实际读取的是限定 Git 元数据，目标源码 blob 未找到；不能将此次访问表述为未接触原目录。

## 文件级来源清单与恢复计划

此表是调查清单，不是新增 canonical 账本。缺少 blob 时哈希不可计算，不以空文件哈希代替。

| 字段 | corrected 目标 |
|---|---|
| source repository identifier | `E:/llmwiki/chanlun-trading-system`（目录及固定对象存在已核对，历史完整来源关系未认证） |
| original commit SHA | 查询起点 `42fbc52a2cc10212288a293e15c630ed09ea59f5`，不是已恢复文件的来源提交 |
| original path | `scripts/run_engine_corrected_phase4_v3.py` |
| Git blob object ID | NOT_FOUND_IN_CHECKED_SCOPE |
| raw byte SHA256 | NOT_AVAILABLE_NO_BLOB |
| target path | `scripts/run_engine_corrected_phase4_v3.py`（计划，未创建） |
| dependency reason | 两个现有正式调用方通过真实 loader 依赖该文件 |
| already present in current main | 否 |
| exact copy or adapted / adaptation diff / adapted SHA256 | NOT_APPLICABLE；未恢复，未适配 |
| source confidence / unresolved provenance | 未找到可信实现；不能从接口名推导原算法 |

后续文件级顺序：取得该精确脚本的可信不可变版本 → 静态列出实际 import、延迟 import、资源引用及顶层副作用 → 仅沿已确认路径读取必要纯源码 → 每文件登记来源提交、路径、blob、原始字节 SHA256、目标、适配差异及适配后 SHA256 → 比较当前 main 同名模块接口，保留已认证实现 → 对部署副本最小适配 → 在业务 import 前安装隔离后验证真实 loader。当前第一步受阻，后续依赖路径和数量仍 UNKNOWN，未执行猜测性恢复。

## 已知接口及加载矩阵

接口证据来自当前部署调用方，不代表已读取 corrected 内部实现。

| 必要入口/接口 | 来源或依赖分类 | 本轮结论 |
|---|---|---|
| corrected loader / executor._corrected_module | 精确第一方脚本缺失 | BLOCKED_MISSING_SOURCE；成功正向未实现 |
| real_runtime → load_corrected_module | 同一个源码 loader | 同上；未调用 RealFactoryRuntimeV1.run |
| legacy.ResearchDataRouter / ResearchDataAccessGuard | 当前包有对应类；legacy 实际绑定与版本未知 | NOT_VERIFIED，不替换绑定 |
| legacy.load_universe_sets / PITStateMap | legacy 原路径与实现未知 | NOT_VERIFIED |
| legacy.load_market_index / market_regimes | legacy 原路径与实现未知 | NOT_VERIFIED |
| legacy.build_store / load_events / sha256 | legacy 原路径与实现未知 | NOT_VERIFIED |
| corrected.run_corrected_candidate / Side | corrected 实现及绑定未知 | NOT_VERIFIED |
| corrected.bootstrap_result / concentration / subperiods / regime_split | 原统计实现未知 | NOT_VERIFIED |
| corrected.recompute_cost_stress_metrics / classification | 原成本与分类实现未知 | NOT_VERIFIED |
| corrected 内部标准库 / 锁定第三方 / 第一方 / 源码资源 / 延迟分支 | 缺原源码，无法分类穷举 | UNKNOWN；没有新增依赖声明 |
| 外部数据契约 | 当前调用方涉及日线、交易日历、PIT、基准、股票池政策、因子/事件 | 数据权限排除；未读取，不作为源码恢复 |
| 现有 17 模块、prompt/schema/config、源码根与数据根分离 | 现有 R1 冷 worker 与独立复制部署树 | 本轮结果由下述新分支 CI 取证，不借用旧 HEAD |

由于没有任何恢复文件，顶层副作用静态检查、真实 corrected `__file__`/spec.origin、全部传递与延迟依赖正向无法进行。不得宣称必要资源数或副作用计数为零。

现有 R1 测试保留不同 cwd、数据根毒化哨兵、复制部署树缺 prompt 负向及两个真实 loader 缺模块阻断；未删除或放宽任何断言。尚无 restored helper，因此“从部署副本移除 helper/必要资源”的新负向及 corrected 成功正向仍 NOT_VERIFIED；现有缺模块测试的通过不代表源码闭包通过。

## 验证与交付边界

本轮仅新增/追加文档，源代码与测试、工作流、依赖锁均与基线一致。本地核验 Git 来源、PR 父子关系、文档差异及 `git diff --check`；不在有 editable 污染风险的系统 Python 上 import 项目。按用户要求，提交并推送后由既有 R1 workflow 在新 HEAD 的干净 Windows Python 3.13 / Linux Python 3.11 checkout 执行原五阶段和 R1 回归，保留所有原选择器、skip 与负向。

CI 在提交时为 PENDING；实际最终 SHA、run/job、版本与文件系统、计数及结论在最终交付回复绑定。代码没有恢复成功，即使回归成功也仅证明已有部分工程能力未回归。无全系统真实数据访问、Performance、Final Test 或订单探针计数声明；仅可报告 CI 实际输出的安装探针覆盖。

## 最小追加资料需求与停止点

仅需补充该精确脚本的可信源码来源：包含它的可读 Git 仓库/不可变提交与路径，或用户明确授权读取 `E:/llmwiki/chanlun-trading-system/scripts/run_engine_corrected_phase4_v3.py` 的当前源码副本并提供可核实的版本来源。本轮没有检查该工作区文件是否存在，不能保证后者可用。无需授权整个工作区、备份扫描或任何真实数据。取得脚本后才能确定必要 helper 的精确路径；不预先请求泛化范围。

本轮停在来源阻断及独立工程复核，不提出算法重写，不合并 main，不启用 auto-merge，不开始 R2。

```text
WORK_PACKAGE=R1_CONTINUE_SOURCE_RECOVERY
BASE_COMMIT=d3dcb68934ea8fb058c98039181d29894b6425df
BRANCH=codex/r1-trusted-source-recovery-v1
ORIGINAL_REPO_SCOPE_CHECK=SCOPED_GIT_METADATA_VERIFIED
ORIGINAL_EXPORT_COMMIT_FOUND=true
CORRECTED_SOURCE_FOUND=false
SOURCE_PROVENANCE_MANIFEST=docs/R1_TRUSTED_SOURCE_RECOVERY_V1.md
RESTORED_SOURCE_FILES=[]
ADAPTED_SOURCE_FILES=[]
MISSING_SOURCE_OR_RESOURCE_ITEMS=scripts/run_engine_corrected_phase4_v3.py; transitive helpers/resources UNKNOWN
UNVERIFIED_LAZY_BRANCHES=ALL_CORRECTED_INTERNAL_BRANCHES_UNKNOWN
DECLARED_ENTRYPOINT_COLD_LOAD=BLOCKED_MISSING_SOURCE
FORMAL_SOURCE_DEPENDENCY_CLOSURE=BLOCKED_MISSING_SOURCE
SOURCE_CLOSURE_CERTIFIED_SCOPE=NO_NEW_SCOPE
REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED
READY_FOR_REAL_TRIAL=false
R1_FULLY_CLOSED=false
REAL_WORKSPACE_RUNTIME_INDEPENDENTLY_VERIFIED=NO
R2_STARTED=false
MAIN_MERGED=false
READY_FOR_INDEPENDENT_ENGINEERING_REVIEW=SOURCE_INVESTIGATION_ONLY
```

PR #6 的已合并事实与本轮 MAIN_MERGED=false 含义不同；后者表示本轮分支未合并。回滚本轮文档：在该独立分支执行 `git revert --no-edit <本轮文档提交SHA>`，不 reset main、不修改原目录。
