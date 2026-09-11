# BaoStock双价格训练账户确认包 V2

状态：接入与合成验证完成，待本包的具体执行批准。承接b08f0c4964872dd189cf649b39a7e22323468afd。不是旧总回报提案V1的直接启用，不覆盖旧合同、原结果或消费。

## 一次批准的具体范围

推荐仅执行一个固定候选BAOSTOCK_HFQ_RETURN_5D_EXPLORATORY_V1。信号为六个相邻独立session上的`HFQ_close[t]/HFQ_close[t-5]-1`，小于0、升序及symbol打破并列、Top-3。BaoStock官方采用涨跌幅复权，不是现金分红再投入的实际总回报，因此不再要求逐事件公告会计条款来生成该信号，也不把它称为V1税前总回报。[官方方法](https://www.baostock.com/helpdocs/pdf/BaoStock复权因子简介.pdf)。

新版本是已看过TRAIN结果后的方法修订，不是免费工程重算、不声称新机制或未见样本外。原新颖性服务必须比较原候选/拒绝/登记历史，判重复即拒绝，不能换名或参数补位。

新增额度建议为主回测1次、仅确证工程错误的修复1次，合计2。旧额度继续用尽，新授额度当前0。一次批准包括本固定计划的数据物化、输入检查、无收益可行性、权威增量登记和主执行；不逐阶段再申请同一范围。无收益门槛继续30条关闭路径、20个开仓日、至少2证券，不能用旧1443路径冒充新信号的路径。

原账户参数不变：10000元、最多3仓、100股订单lot、下一session开盘、固定持有3session后按原退出流程下一开盘退出、佣金0.00025最低5元、卖印花税0.0005、滑点0.001、参与率0.10及原保守容量交集、T+1/FIFO/停牌/限价/账本。

## 接口及输入计划

保留原5182成员和历史状态，不以64只曾成交事件样本或122只缺源成员定义新股票池。重用原日历/状态身份；已取得122只价格覆盖不是全池双价格输入已经就绪的证明。

通过原BaoStock Provider session调用`query_history_k_data_plus`，frequency=d；同一固定获取批次对每个必要成员读取adjustflag=3的RAW及adjustflag=2的HFQ。RAW字段date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST；HFQ字段date,code,close,adjustflag。仅2022-07-22至2024-07-31，评价TRAIN为2022-08-01至2024-07-31。每证券每价格模式最多一次初次获取，记录所有返回/失败；不联网购买、不获取Validation/Final Test，不再获取公司行动公告。依赖API响应字段须校验，拒绝字段不支持或日期范围不符。

数值文件写到新版本根，原件只读，每份绑定真实SHA256、查询参数、获取时间和Provider版本；顺序分证券落盘，不一次建立全池Python字典。不把TDX原价与BaoStock HFQ混配；旧字段欠缺preclose的缓存不能伪造填齐。输入物化和结构检查不计算真实信号或绩效；真实信号/排名在批准的登记边界内才可计算，并记录信息访问。正式Trial仍0。

全池、适格日及六session依赖预检必须通过；缺失/停牌空量/无下一session均保留并标不可计算。使用原历史池预检，不能静默删除候选成员后仍声称完整Top-3。实际全池RAW/HFQ版本目前尚未取得，故input_identity尚未填写；不得制造hash。由已批准计划的物化步骤产生身份，随后冻结真实执行回执，不用人工编写JSON授权。

## 价格、时间、公司行动边界

- 新PriceViews适配器只将RAW OHLC、成交量、金额及原preclose交给MarketDataStore；HFQ只生成特征。价格模式及证券不符拒绝，按独立日历shift5，不按记录序号跳过缺日。
- 信号最早源日之后下一session开盘可见；已有更晚时间优先。历史实际发布时间UNKNOWN不改写为模型时间。最新获取HFQ属于回溯数据版本，不声称历史当时版本PIT。
- 复权信号不能为持仓自动创造分红现金或送转股。原29990条本地hazard及意外事件PARTIAL处理不放宽；不宣称BaoStock复权因子是完整会计事件证明。本轮不续取公告，不启用个人红利税、零碎股或未知退市清算新会计规则。
- 原hazard筛选包含事后已知事件，存在条件筛选偏差；所有输出只能是此假设下的降级模型结果，不能外推为真实可实现净收益。完整会计或新股票池不是本包暗含授权。

## 权威额度及执行接线

Objective：RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1。

预算仍为`E:/llmwiki/autonomous-strategy-research-v1/revised-exploration-v1/governance/search_budget_registry.json`，只读SHA256为`ded49a08f56bf422808a99876ce2d5779c962744401f1022889f7874fcea13b1`。原DegradedGovernanceV1.summary实际返回MAIN=1、REPAIR=1，读前后hash不变。旧12次消费/争议、四探索及信息曝光全部承接。

新增BaostockGovernanceV1使用同一SearchBudgetRegistry的register_train_execution_increment，独立版本回执，不改旧桶。继承父授权、较早期限、撤销、幂等、reserve/start/settle及red/green修复证明。输入配对、历史池、来源及canonical novelty decision缺失时拒绝确认。run_baostock_account复用原账户执行函数，核验新合同身份、信号版本、RAW标志；旧降级回执不匹配即拒绝。

资源：并发1、数值线程1、worker900秒/2048MiB；本计划数据工程及研究计算累计不超过90分钟，其中两个账户执行累计不超过1800秒；原更早/更严限制优先。截止2026-09-14T10:05:03+08:00，不自动延期。取得数据前不能承诺全池下载一定在此额度内完成，耗尽保留断点并停止。

## 已完成和剩余步骤

已完成双价格适配、原订单/成交/退出/账本合成联调、新版本账户及同预算治理接口。合成验证覆盖除权机械跳变、数值缩放不变性、未来行不影响过去信号、缺日、停牌空量、重复/跨窗、错证券/模式、迟到时间、原始成交价、旧回执拒绝，以及原撤销/重复/到期/修复额度回归。

尚未启动新的真实数据获取、真实信号、无收益候选路径或账户执行；尚未登记新额度。收到本具体包的批准后，先按以上界限物化并绑定真实输入，实际通过完整池、来源、新颖性和可行性检查，再完成原资源限制器内的主执行。任何失败如实归档，不因结果差使用修复额度。平台/父授权拒绝不得绕过。

最终结果只为BAOSTOCK_HFQ_SIGNAL_RAW_ACCOUNT_EXPLORATORY。保留完整订单、成交、拒单、lot、现金/持仓/费用与模型描述指标。无p/q、正式资格、V4、Paper、订单或实盘授权。STRICT_TRAIN_INPUT_READY、READY_FOR_REAL_TRIAL、R1_FULLY_CLOSED、AUTONOMOUS_STRATEGY_GOAL_COMPLETED继续false。Windows OPEN及失败保持，不merge/push。
