# 原两账户用途的有限限时续接

本协议只支持已批准、暂停且无活段的同一任务，将原BASE和STRESS两个ACCOUNT用途的累计活动上限由28800秒改为72000秒。每个用途增加43200秒预留，共86400秒，从原campaign和EXPLORATION余量扣除；原已付费用仍计入累计。原profile、单worker900秒/2048MiB、campaign总预算、期限和账户经济身份保持。

已有零日版本接续许可必须存在。新许可独立保存在任务的`account-purpose-time-extensions/<extension_id>/MANIFEST.json`；不得再建立零日许可，也不得覆盖旧JOB、START、plan、CONTROL、收费或许可。后继plan仅投影前驱`runtime.source_hashes`中的已批准治理源码。经济依赖和持仓、lot、订单、退出状态不能转换或重跑。

## 准备和Owner批准

先在旧固定版本通过公共`control_job(job, 'PAUSE')`建立暂停边界，等待相关进程退出并核对无active/UNKNOWN。归档治理旧源码与旧核验回执；新代码、精确提交CI及带人工成交和公司行为的完整双成本504日合成必须先通过，再安全同步主目录。

`source_inventory`只读原许可库存。最终spec必须包含以下字段：

- `authorization_id`、`task_ref`、`predecessor_manifest_ref`绑定原任务与已使用的版本许可。
- `source_replacements`包含每条旧SHA、原字节归档引用和部署后实际新SHA；新增文件只加入`runtime_source_refs`。
- `economic_source_refs`完整列出前驱经济依赖，字节必须保持。
- `proposal_ref`和`ci_ref`绑定人类批准的方案及最终验证记录。CI包装记录须为`PASSED`，含精确40位head，以及与summary排序、内容一致的`runtime_sources`、`source_replacements`和`economic_sources`，并附实际CI、完整合成证据引用。

新增治理Python源码通过`.gitattributes`固定为LF，避免平台检出改变用于Owner绑定的字节。部署后仍须按主目录实际文件重新计算SHA，不能用工作区的提案SHA代替。

通用CLI只规划、冻结或读取证明，不获得Owner能力，也不扩大预留或派发：

```text
python scripts/setup_account_purpose_time_extension_v1.py plan --spec SPEC --spec-sha256 SHA
python scripts/setup_account_purpose_time_extension_v1.py freeze --spec SPEC --spec-sha256 SHA --approval APPROVAL_REFERENCE --approval-sha256 SHA
python scripts/setup_account_purpose_time_extension_v1.py inspect --manifest MANIFEST --manifest-sha256 SHA
```

`APPROVAL_REFERENCE`是已登记Owner审批的引用包装，不是能力令牌。原受信入口须先核同一Owner目录的ACL，再沿本次明确人类批准以对象能力审批最终summary。对象能力不得从文件读取、序列化或写入日志。

## 原子安装和恢复

`ResearchCampaignV1.install_account_purpose_time_extension(manifest_ref)`追加一次`CAMPAIGN_ACCOUNT_PURPOSE_TIME_EXTENDED`父事件，同时增加两原用途的预留。容量不足、暂停控制头或checkpoint变化、活段/UNKNOWN、到期、业务源码变化均拒绝首装；不允许只安装一个用途。重复同一已安装证明不再追加事件或扣额。

父事件和子镜像必须分别核实。缺少`ACCOUNT_PURPOSE_TIME_EXTENSION.json`时，普通执行入口拒绝派发；专门调用原`reconcile_segment_mirrors(name)`补全同一证明镜像，再严格读取母子链并结束为WAITING。该对账调用不派发，后续独立会话才恢复公共控制链和`run_account_version_session_resume_v1.py`。不要同时使用会立即调度的`submission.resume`。

新段的dispatch、worker、输入访问、status/resource、charge/resume和最终报告反馈携带同一quota证明。旧段保持原样；没有测量RESOURCE的旧未知段保留派发上界收费及`measured_seconds=null`，不得伪造实测回执。累计上限不足协作窗口时拒绝派发，72000秒仍不足则保留真实未完成状态，不自动再次扩限、退款或补造FAILED。

聚合检查可以在一次调用内复用刚完成完整复验的治理状态，但仍须逐段读取并校验版本与资源等侧车。公开`validate_dispatch`每次重新取得完整治理状态；不能接受调用方传入的状态，不能跨调用缓存Owner许可。聚合前后须完整复验Owner、源码、scope及账本，并核对权威`run_budget_events.jsonl`引用未变。补写任何缺失的扩限镜像、派发/收费镜像、不可变修复标记或版本凭据前，也须重新完整复验并在复验前后核对同一事件流引用；只有末尾拒绝不能撤销已经写出的不可变凭据。下层对账在装配父段列表前固定本轮事件流引用，首次写入同时执行自身的新鲜权限/证明检查和上层完整校验；已存在原件仍逐字节核验。

该路径的回归须同时覆盖第35段已有原DISPATCH但尚无worker或版本凭据的边界：遍历中撤销Owner批准、改变治理源码或篡改较晚段侧车时，应拒绝且不新增修复标记或版本凭据，原DISPATCH、checkpoint及历史收费保持原字节。性能证据使用同一人工用例的进程内剖析，记录源码SHA和完整测试结果；嵌套累计耗时不能相加，人工调用热点不能代替真实全池900秒或盈利验收。

安装前可弃用隔离分支，真实原件不变。安装后不得通过删事件、覆盖许可或重置费用回滚；需另行获得具体授权。旧版本历史有效回执与升级后能否在线重新核验应分别报告。


## 日内治理检查与性能证据

每个账户日仍在日首及checkpoint提交后分别执行fresh guard，账户引擎/撮合/状态源码不改。一次active_execution内部只装配本轮完整的新鲜父视图，绑定文件、Owner/源码、原START/消费及父ACCOUNT/DATA trial身份/消费/完成均重新核对；视图不存self/global，不跨调用复用。公开active、peek_status及resolve默认行为保留，首次immutable写前Owner/h0和末尾strict不共享该日内视图。

观察路径不再为获取运行状态重复持锁写预算快照。缺少父trial镜像或身份冲突时只读拒绝，不补记费用/消费，须沿专门对账后WAITING处理；下一次guard必须重新读当前证明。

已有真实元信息只证明原loop003/004各至少52分钟推进14/15日（16–17日/墙时小时）；活动墙时与纯账户计算、复验等待需分开。人工连续binding基准复现每guard23次完整campaign_view，初步局部视图降至3次；实际性能及权限反例以当前XML为准。人工基准不包含3401证券全池计算，不能宣称真实账户日吞吐已经提升。

2026-10-14原R5中断且无完整XML/RESULT，旧结果保留；后续必须独立新鲜用途运行当前字节回归、完整504双成本合成及精确CI。

完整人工分红场景在冻结前须通过公开扫描的合格范围校验。事件`source`绑定公开提供器实际登记的`events`来源；直接引擎夹具也登记相同事件内容摘要，不能只用preview证明公司行为可用。该入口断言保留原分红来源、日期和税务校验，拒绝结果及失败用途原件保留。


## 同次重放与引用读取的边界

只读 `peek_status`、账户guard及有效额度查询直接使用本次预算构造中已经完整核验的视图；该视图只经局部返回值传递，不存实例或全局，下一次调用重新读取全部权威事件、Owner、源码及逐日文件。变更路径仍在构造完成后、trial镜像处理前做fresh，处理后返回视图再做fresh，派发和收费提交后的尾部复验也保留。不能把构造时验证替代后续写前授权。

扩限引用成功读取由安全读取器完整检查打开前后路径及已打开文件对象，去除调用方第三次重复的路径组件遍历；越界、父链接、重解析点和对象替换拒绝规则不变，失败继续沿用原PATH_INVALID或REFERENCE_CHANGED错误分类。每次调用仍完整哈希源码及各个日文件，不使用mtime或先前CP哈希作为日文件校验的替代。

人工guard回归实际完整重放为1次，派发/收费边界各保留4次；单引用路径组件遍历为2次。实时人工合成采样只用于定位函数热点，不能从调用次数或采样百分比推断真实全池吞吐；最终主目录部署仍要求修后精确CI及新鲜双成本完整504日成交/公司行为合成。
