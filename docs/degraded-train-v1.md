# 独立降级训练预检 V1

本轮依用户附件从 `0b799b861e50ade4a263861ba587f72ab3dbe29c` 继续。范围仅为 `DEGRADED_TRAIN_ACCOUNT_BACKTEST_V1` 的事前合同及无收益可行性扫描。严格 validator、原费用、策略参数、历史预算和 Windows OPEN 事件均不修改。

执行顺序：冻结合同和精确读取清单 → 合成规则测试 → 限资源读取既有 TRAIN 派生输入 → 无收益扫描 → 固定门槛裁定。本次扫描没有通过，依批准停止；没有建设或调用一个可绕过此结果的账户执行入口，没有登记条件尚未满足的增量预算。

## 实际输入与独立身份

输出根：`E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/degraded-train-v1/`。

`READ_ALLOWLIST.json` 固定全部读取对象；`FROZEN_CONTRACT.json` 固定批准文件、输入、代码和预算原始 SHA256。读取原 materialized-v3 的独立日历、日线、原 RETURN_5D 及历史状态，以及已经窗口化的 gbbq 和预热缺格证据。不读取原始 DAY、TQ、全量 gbbq、Validation、Final Test 或旧绩效。

5182 个冻结证券全部保留，包括 146 个缺源证券。每天输出可观察、不可观察、因子可计算、资格及排名计数；缺失不填零。2673 个 NOT_YET_LISTED、27 个 UNKNOWN 保留原证据。已有暖区日线不等于完整暖区状态：原状态文件只有 TRAIN，故最初五个训练日六 session 状态依赖尚不完整，最早资格日期为 2022-08-08。这是延迟资格，不是永久删除成员。`FIRST_ELIGIBLE_SESSION.json` 的资格指因子和状态依赖就绪，在 RETURN_5D<0 条件之前；null 表示本窗口没有达到该资格。

只使用前一 session 因子作下一 session 开盘候选。已有真实 `source_published_at` 若晚于模型时间则拒绝；UNKNOWN 不改为真实发布证据。状态文件中的旧模型时间不冒充观测发布时间。

## 成交量前提的具体裁定

项目 `tdx_data.py` 证明原 DAY 偏移 24–27 字节是 uint32 成交量数量字段；这证明非负整数表示，不证明单位全集。两份既有 Skill 的 get_market_data 返回说明分别写 TQ Volume 为股、手，描述的是接口返回值。既有 OWNER 样本比对没有证明原 DAY 与接口间的单位转换：旧 1、100 两种转换均未全行通过，`units.json` 仍为 `UNIT_SOURCE_CONFLICT`。

因此不能把“允许检验这两个假设”直接写成“原 DAY 的单位必然只属于这两个假设”。本次独立结论为 `DEGRADED_VOLUME_MODEL_UNUSABLE`，不是重新要求原严格单位唯一判定通过，也不是要求重跑 OWNER。`VOLUME_MODEL_EVIDENCE.json` 保存这一具体缺口。

容量规则已用手算边界验证：H1、H2 分别计算 floor(0.10×数量)，交集取较小值；零容量绝不回落为全量成交。候选路径保留两组**假设性**数量和上限，因适用性未成立，accepted_qty=0。假设数值不能冒充实际可成交容量。

最小补证对象是**当前来源版本的原 DAY 格式／导出说明**：偏移 24–27 uint32 的数量含义，以及其允许单位确实只为 SHARES 或 LOTS_100_SHARES 的依据，并绑定来源版本和编码关系。无需现在确定两者唯一值，也不需要重取行情。若只能作为建模假设接受，则须由用户明确变更该前提；本次不自行作此决定。

## 可行性口径与结果

固定 RETURN_5D<0、因子升序、symbol 升序、Top-3；无收益扫描没有持仓账本，Top-3 是逐日独立候选，不是实际账户订单或成交数。计划路径从信号 session 至 entry+4 的最早退出开盘，包含边界；容量与 hazard 并列记录，174 条重叠不能和容量失败相加作为互斥拒绝数。全部 29990 条原事件都参与，不按类别筛掉未映射事件。

扫描 486 日，481 日形成 Top-3，合计 1443 条候选路径。1428 条因容量模型适用性不成立不能放行，15 条因 TRAIN 末端无完整计划路径。没有合法可放行路径，因此无需读取未来收益或进行账户关闭路径模拟即可拒绝绩效入口。`closed_paths=0` 是通过现有合法性门槛的数量，**不是已经模拟出零笔成交**。

冻结门槛仍为至少30条可关闭路径、至少20个可入场日期、至少2个证券；全拒绝不能通过。结果 `DEGRADED_ACCOUNT_BACKTEST_NOT_INFORMATIVE / INSUFFICIENT_EXECUTABLE_EVIDENCE`。不能据此判断策略盈利、亏损或给出 LOW。

## 复现与恢复边界

入口：`.venv/Scripts/python.exe scripts/run_degraded_train_v1.py`（设置 PYTHONPATH 为当前 worktree/src）。采用原资源 worker：并发1、900秒、2048MiB、数值线程1。真实扫描耗时见 `WORKER_RESULT.json`。结果存在后入口拒绝重复执行，读取已有证据即可；不得删除结果来重新运行。

任何变更必须保留本版合同和失败记录，不原地覆盖 `immutable` 文件。本次代码没有绩效入口、预算登记或自动后续任务；获得新证据或明确修订批准之前停止。修复额度未动，三个正式完成／就绪标志为 false，STRICT 与 DEGRADED 的输入就绪状态分别为 false。

验证：容量交集、零容量、未证实 schema、负数、hazard 闭区间、联合门槛及原严格输入／账户回归共16项通过。实际无收益扫描成功结束，全部原输入哈希一致，权威预算 SHA256 未变。不是正式 CI 或严格输入认证。
