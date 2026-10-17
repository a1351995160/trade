# 失败账户恢复的启动校验去重

本次改动针对完整合成普通账户段在启动检查中消耗全部协作窗口的问题。原 R4 的普通 52、53 段分别自然结清 832.7594915、832.7856911 秒，均为 `CONTINUE`，没有新增账户日；它们不记为完整合成通过或产品 `FAILED`。

## 实测依据与边界

停止并保全原 R4 后，在其固定人工根上只运行一对有界、只读的 ON/OFF 来源校验。两臂均自然退出 0，来源、277 日前缀、人工账户文件、人工 Owner 和父账本前后字节不变，没有账户收费或派发。原监督限制保持为请求 900 秒、宿主 890 秒、协作截止 830 秒、2048 MiB、进程 2、线程 1。

- ON：`validate_sources` 634.927519 秒，完整 context 校验两次，分别 318.087878、316.721847 秒。
- OFF：`validate_sources` 344.090484 秒，完整 context 校验两次，分别 172.284062、171.719629 秒。
- 两臂各完成 44 次 `Recovery.read`，每次完整 context 校验包含 22 次。嵌套 inclusive 时间不能相加为总耗时。

ON 先于 OFF，文件系统及模块启动状态可能存在顺序偏差，不能把差值称为纯 trace 因果比例。OFF 保留显式检查和绝对截止，仅用于反事实归因，不能用于交付或真实派发。两臂启动时都保留了 `sitecustomize` 的 `ModuleNotFoundError: scripts.probe_all_indicator_strategy_v1` stderr；该错误发生在人工 capabilities 替身安装前，但已有部分正常模块导入，后续真实 `validate_sources` 均完整返回。诊断协作时钟从 `child_arm` 开始，早先 Python bootstrap 由宿主 890 秒监督覆盖，没有计入上述来源校验时间或 trace；不能据此声称完整 worker 启动预算已验证。该配对不证明原 R4 装配完全相同、后续治理/loader/首次 guard、完整 worker 启动或经济日可以完成。

原件在主树 `reports/continuous_universe_real_acceptance_20261010/initialization_recovery_20261012/R4_VALIDATION_TRACE_PAIR_R2_20261016T192800Z/`。`PAIR_RESULT.json` SHA256 为 `b9689f54cc756c81ed3a66caea5a6ac5e7cc9eb44fe3d7dbc2be0f920a3365fa`；原 R4 受控停止回执、空 XML、日志和已结清费用保留，不重标 PASS。

## 最小改动

`runner.validate_sources(job)` 每次仍重新调用 `account_version_context(job)`。其 `from_job` 已完整核验 context、Owner、来源、日链、费用及原 JOB 路径和完整字典；删除紧接其后的同模式重复调用。公共 `context.validate_sources(job)` 本身保持完整 fresh 校验，旧来源分支、report repair 冲突及后续派发权限检查保持原语义。不增加绕过标志或跨调用验证缓存。

启动监控保留全部 Python 行、同一个跨帧 64 行截止计数、绝对时钟、`before_call` 和 trace 恢复。仅对没有既有 trace 的普通新帧简化 local 分派；已有调用帧、生成器、协程、控制帧及既有 trace 继续使用完整路径。启动完成后不能重新安装监控，诊断提交和收尾仍消耗原段时间。没有延长预算或跳过任何来源字节、Owner、日链及侧车检查。

## R5 反例与同次恢复证明读取

精确 `61a9bc1f` 的 91 CI、局部 75/90 均通过，但完整 R5 的普通 52、53 段仍分别结清 832.6726687、832.7786847 秒且零新增日。第 52 段来源校验 278.6931223 秒、治理阶段 242.0897305 秒均完成，准备与经济入口前阶段在 305.1976484 秒后截止；这些是阶段耗时，不能把最后一个阶段全部归因于一个函数。两段都是自然 `CONTINUE`、exit 75、非 timeout，随后完整 R5 受控保全，`natural_exit=false`、`changed_tested_sources=[]`、没有第 54 段，不计完整 PASS 或产品 FAILED。

单次人工 R5 只读 ON 诊断已保全。自动 `sitecustomize` 和完整人工 capabilities 回调装配检查通过，但外部读取白名单随后拒绝 `validate_sources` 的早期读取；没有完成来源调用，也没有进入 `Recovery.read` 或 active callback。全部冻结字节相同、零账本收费。该结果不提供有效调用次数或性能结论，不重试 claim，也不据未记录的具体路径猜测错误归因。

当前最小候选去掉一次公开读取内部对完整恢复证明的递归重复读取：

- `_job_manifest_reference` 只定位原 JOB 命名空间中的唯一 manifest，不授予权限。公开 `FailedAccountRecoveryV1.from_job` 仍立即完整读取并核原 JOB；公开 `require_predecessor`、`require_capability_bridge` 和 `validate_predecessor_sources` 也保持完整 fresh。
- 扩时读取的提供对象和自动发现两条路径，都在原 A 位置核恢复 manifest、root、base、新恢复 Owner、规范 slot、原 JOB 和确切前驱。外部对象只作 locator，不信其 summary。原 C 位置仍完整读取来源、归档、277 日、原失败和费用，再核前驱摘要并投影；不把 locator 当验证完成标记。
- 历史 bridge 在自己调用内先认证 Owner，并按绑定引用读取受限 Owner 中的桥原件，随后在原最后前驱检查位置另做一次完整恢复读取。桥读取期间持续出现的撤销或源码变更必须被最后检查拒绝。普通 bridge 分支不变。

早期恢复 Owner 拒绝保留；原 A 对完整来源与费用的失败时点有意移至 C。没有锁或 epoch 协议时，不宣称能观测所有瞬时变化后恢复原字节的 ABA 竞态。每个公开调用仍重新读实际原件，不缓存跨调用 Owner、来源、日工件或校验结论。三个进入经济计算前的 guard、父状态和镜像核验、99 经济来源及 900 秒/2048 MiB/线程并发 1 均不改。

静态预测为每个扩时公开读取完整恢复 1 次、历史 bridge 的子扩时和自身各 1 次、单个已启动账户的完整 context 从 22 次减至 11 次；仍需真实调用计数和完整合成验证，不能将比例当作整轮提速。新增只读反例共享一次原合法 277 日/50 FAILED/费用生命周期，全部原构造断言保留；每次公开验证仍真实执行。反例覆盖两条发现路径的早期 Owner 拒绝、原件/费用晚变更以及桥读取期间的变更，修改的人工原件在 `finally` 恢复。

## 验证与部署

入口单元复用已合法签发的 V1 context 和六段历史费用，验证每次入口仅委托一次完整 fresh 校验，以及来源、费用、JOB 变化和 report repair 冲突仍被拒绝；篡改前先确认原公共派发权限校验可以通过，篡改后必须零派发、零写入。原 277 日、原第 50 段 FAILED、全部费用与日链的完整恢复断言继续由既有恢复测试和完整双 504 合成执行。R10 重复装配该完整生命周期超过半小时后受控保全，`natural_exit=false`、`changed_tested_sources=[]`，没有有效 JUnit，不计 PASS 或产品 FAILED。

实现后重新验证新字节的必要回归、精确 CI、完整人工双 504 日与双原生连续对照、逐日核账、报告、公共 FEEDBACK，并独立复核实际工件。旧 833 的 91 CI、旧 68/90 和受控 R4 不能代替新字节门禁。

当前原真实研究账户仍为 277 日、原第 50 段 `FAILED`，全部费用保持。全部门禁通过后才在原 Owner 根限时安装，分调用 repair/strict/WAITING，再以原公共有界会话从第 278 日继续。各 72000 秒、global 288002 和原绝对期限不变；再次失败或到期即停止，不自动扩限。

本轮回滚点为生产分支提交 `61a9bc1f4c1df6a83fe4a263ff1e1ab4f03a0639`；门禁前只影响隔离生产工作树。旧 61a9 的通过结果保留，不证明新字节。保留所有原测试和失败原件，不修改部署 main 或真实账本。
