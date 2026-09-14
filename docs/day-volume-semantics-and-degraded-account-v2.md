# DAY量纲证据与降级账户接线 V2

当前任务从f7425f87fb925ee958eb647e3f8f81cba3508d19继续，唯一改动用途为DEGRADED_TRAIN_ACCOUNT_BACKTEST_V1。原f7425f8容量拒绝、原strict validator、1-share容差失败及旧预算保留。

## 事前规则与实际证据

`scripts/prove_day_volume_v1.py`先冻结规则、5036个已有精确文件路径、四个原样本、源码和快照身份，再由原资源worker执行。使用全部TRAIN bar，没有根据结果更换样本。仅日期键允许在窗口外读取；行情记录在判定TRAIN后才读，禁用缓冲预取；四个明确要求的整文件哈希为不解码的身份读取。没有TQ查询、OWNER导出或gbbq全量解析。

H1为股，H2为百股手。事前固定eps=0.001；全部及沪深各市场、四个样本都要求胜者中位数在[0.8,1.25]、[0.5,2]覆盖≥99%、[0.8,1.25]覆盖≥95%、带eps的OHLC覆盖≥90%；败者[0.5,2]覆盖≤1%；最少样本分别100000、10000、100。所有组必须方向一致且两假设明确相差100倍。

2,370,849条TRAIN记录全部有效，排除0，当前原DAY与冻结快照冲突0。H1中位数0.999508768856，H2为0.009995087689；H1带eps OHLC覆盖100%，H2为0%。全部、沪深市场和四个固定样本均通过同一冻结规则。三个数量级偏离点仍保留，未删除。log10直方图边界字段列有限内部边界，计数首尾包含下溢／上溢桶。

证据根：`E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/degraded-volume-semantics-v1/`。包含事前注册、5036份实际布局/窗口哈希、四个整文件哈希、逐组量纲分布、访问账及公开快照。

公开证据分三个层次：

- [官方函数列表](https://help.tdx.com.cn/gspt/docs/markdown/redword/functionlist.html)：公式VOL为手、AMOUNT为元，不对应底层偏移；本次完整页TLS/抓取失败，保存实际搜索索引摘录及失败。
- [pytdx原实现](https://raw.githubusercontent.com/rainx/pytdx/master/pytdx/reader/daily_bar_reader.py)：32字节布局及原row[6]，A股DataFrame输出另乘0.01；不能把DataFrame单位混为原字段单位。
- [独立Go读写实现](https://github.com/injoyai/tdx/blob/v0.0.87/extend/local.go)：股票DAY为股，接口量为手，读写以100换算。Python和Go为不同实现家族，不是多份厂商证明。本机pytdx不额外算一份独立公共来源。

因此合同为`TDX_DAY_VOLUME_SEMANTICS_V1`，等级`DERIVED_AND_PUBLICLY_CORROBORATED`，仅降级用途、永久`NOT_FOR_QUALIFICATION`。不是厂商当前版本背书。旧四样本1968行全部与DAY值转float32后的数值一致，支持量化差异解释；内部提供者原因仍未直接观测，原6/3/15/19与1-share失败不改。

## 降级可行性与原引擎接线

入口为`PYTHONPATH=当前worktree/src`后运行`.venv/Scripts/python.exe scripts/run_degraded_account_v2.py`。`--feasibility-only`供同一施工中的结果冻结阶段使用，后续默认入口复用已完成扫描继续主回测；存在主执行记录则不免费重跑。各版本工件不可覆盖。

输出根：`E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/degraded-account-v2/`。

新扫描复用经哈希核验的1443条原候选，不按容量或公司行动筛出替代候选。计划入场至entry+4最早退出开盘，采用原价格限制/状态模型、前一有成交session的量、H1/H2交集和静态10000/3金额检查两侧完整数量。它是可执行候选路径证明，不是实际账户交易数或未来回报。结果911条闭环、438入场日期、483证券；174条hazard拒绝、333条容量或整手金额不足、其他状态/退出拒绝10条、末端15条。原30/20/非单一证券门槛未改。

账户侧复用Signal、OrderIntent、Sizer、Risk、OrderManager、Broker、Fill、PortfolioExitEvaluator与CorporateActionAccountingV1。只增加独立接线：

- 前session输入在下一session开盘才可见；先由原引擎处理已到期退出，再生成本次买入，并显式将原NEXT_SESSION语义绑定到输入源session。没有把generated_at倒填至前一收盘。
- 选股使用已满足连续六session依赖的原因子和前session状态；当前session状态只用于成交现实拒绝，历史发布时间仍UNKNOWN。更晚真实源时间沿六session依赖传播。
- 原DAY数量保持原值；容量交集、整手及实际成交量分别记录，零容量不沿用旧FillModel的全量回落。费用、滑点、持有、排名及Top-3完全不变。
- 全29990条窗口记录作为hazard。计划路径有hazard则拒绝、不补位；持仓延长撞事件则标记lot、阻断其交易和后续新增买入，其余持仓可继续退出，最终PARTIAL_UNSUPPORTED_EVENT且完整账户metrics为null。
- 无hazard路径使用空事件会计账本表示“此次没有执行事件会计”，不是证明全部公司行动不存在。386只零记录证券仍只是本地无记录。

同一SearchBudgetRegistry以独立plan_id登记1主+1修复；回执明确新用途和原Objective/父回执。不改旧12次和四次探索记录。原锁、预留、先消费再计算、结算、撤销、到期、重复拒绝和修复证据检查全部复用。旧正式路径继续false。合成测试发现买入/退出组合身份不一致导致迟一session，已在真实曝光前修正并验证，不能扣成真实修复曝光。

实际主回测开始/结束、额度及结果，以同根EXECUTION_SUMMARY.json与execution/<实际id>/completed.json、result.json为准；本文不提前声明成功。资源并发1、数值线程1、每worker900秒/2048MiB，账户累计1800秒，原到期不延长。恢复时先对账，不删除工件重跑。未使用修复余额不变为第二策略。


## 实际主执行失败与受限修复

主执行77635c2c61996a75150e7404d85367866ba4cb5eedab0ad37b99373c5043ddf7于2026-09-11实际开始，主额度已消费1并以失败结算。原Ledger的_next_session_open_from按自然日+1；2022-08-26周五买入生成周六可卖日期，CorporateActionBacktestEngineV1的独立日历检查抛SELLABLE_DATE_NOT_IN_CALENDAR。原failed_account、process与结算保留，不将失败改成未曝光。

合成周五案例复现同样错误。仅在独立降级引擎将非session可卖时点向后投影到首个真实session开盘，再交原公司行动检查；记录old_time/new_time，不提前可卖，不改原Ledger或严格引擎。回归证明周五买入、周一可卖、entry+3后下一开盘退出。41项测试通过，包含原严格输入/账户及唯一修复消费。

修复入口增加--repair-proof，复用原回执/预算的匹配red/green证据，要求当前干净fix_commit与green全部代码哈希一致、affects_input=false、原输入与合同相同。原FEASIBILITY及INPUT_READY不覆盖、不重跑；修复执行和摘要进入新execution_id及REPAIR_EXECUTION_SUMMARY.json，保留原EXECUTION_SUMMARY。若修复仍失败，不存在第三次可用额度。

## 已完成的修复执行与停止

修复执行0a0ccd0c85bb1e40c325b1f7ef1fd00a5ef8994dd553a74228d1548f18399962已由原服务完成结算，状态COMPLETE；主1、修复1，剩余0。修复代码提交58f9f5334d483351390ee7090a6a79a4f512ff1f，65次只向后日历投影有原始审计。期末权益4539.2946元，训练模型净回报-54.607054%，最大回撤63.139987%，关闭277个lot，期末无持仓、未支持事件lot为0。候选优先级LOW，不进行调参或追加搜索。

交付位于同一输出根的FINAL_REPORT.md、FINAL_SUMMARY.json、RESULTS_INDEX.json和RESULT_ACCESS.json；LEDGER、SIGNALS、ORDERS、FILLS、DAILY_ACCOUNT、DAILY_HOLDINGS、REJECTIONS均只是已保存原结果字段的带源哈希拆分。45项索引逐文件校验；现金、费用、税、整手、三仓及结算一致。原主失败仍保留，修复worker输入访问原MAIN标签由补充记录明确为REPAIR，不覆盖原件。精确绩效已在当前会话曝光并只向请求用户交付。

STRICT_TRAIN_INPUT_READY、READY_FOR_REAL_TRIAL、R1_FULLY_CLOSED、AUTONOMOUS_STRATEGY_GOAL_COMPLETED均为false；语义证明、降级可行性与账户完成分别为true，不代表正式资格。原146/27/29990缺口和hazard、批准到期、旧预算消费、Windows OPEN保持。不得再次运行默认入口或修复入口；本用途额度已经耗尽。
