# 免费替代来源完整行情覆盖核验 V1

本轮从cd2cc7da77d9fabb1a9ecd472c1a94d045e5ec5a承接，用户要求沿已打通来源核验覆盖、再判断是否能继续总目标。仅获取/核验输入，不启动策略或改变预算。输出根为E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/alternate-source-coverage-v1。

## 已取得的实际结果

先冻结122只曾ELIGIBLE的旧缺源证券全部身份、2022-07-22至2024-07-31窗口、字段、无重试/并发1/900秒上限，再使用既有BaoStock Provider session获取日线。122请求全部完成并非空，共50,196行，102.337秒；未查询Validation/Final Test。

与已冻结历史表核对，34,174个可入选证券日缺行0，ST/停牌状态冲突0。进一步包含ST和停牌的全部有效生命周期49,464个证券日，缺行也为0。额外返回预热记录保留，不以此宣称原预热状态证据完整。

初始invalid_ohlc_days字段合并了价格、量额和标志验证，计2,565条。定点检查证实全部OHLC有效、tradestatus=0且volume/amount为空，是明确停牌记录，不能称坏价格或填零交易。2,554行在既有状态表为INELIGIBLE，11行是没有旧状态投影的预热行。原初筛与两轮定因证据均保留。

结论：122只证券在原历史表所覆盖的有效生命周期内，行情来源可得性已验证，不需为了TDX缺源删除这些成员。此结论不是新输入已适配完成；仍需Provider单位、事件、时点和原引擎输入合同匹配。原TDX文件未覆盖，新数据只保存在新根。

## 公司行动覆盖与差异

使用前轮65个已发生买入回看期category=1重叠所对应的全部64只证券，冻结精确日期范围，调用query_adjust_factor；仅核对事件日期，不使用因子重算价格或信号。64请求全部成功，返回140条，本地130个category=1日期全部找到；对方另有10个日期。

- 5个额外日期本地同日只有category=5，且provider backAdjustFactor=1，可能为初始基准；未证实，不转换成分红。
- 5个额外日期本地没有任何类别记录。按明确假设追加对应5只证券operate年2023的query_dividend_data查询（整年均在TRAIN内），均无同日分红匹配。保留原条款响应，差异尚未定性，不能武断称TDX漏分红或BaoStock错误。

64只并非全部5182成员，日期匹配不证明金额/股份到账/公告可见性及全事件覆盖。query_adjust_factor有start/end，可用于日期核验；query_dividend_data只有year/yearType，2022/2024全年可能越过原窗口，本轮未扩读这两个完整年度。需要精确窗口化条款来源或相应边界决定，不能先全读后过滤。

## 对总目标的含义

取得必要数据并通过新输入适配后，可以继续总目标。现在已经解除122只证券的来源不可得性；没有理由为了这个问题事后缩小股票池。仍须解决事件差异/条款、绑定新版本输入并通过原数据/结构/新颖性/无收益检查。不能把表内完整覆盖或旧状态一致当作独立厂商完备证明。

原主1、修复1额度已用完，本轮新增策略曝光0。上一份新主1+修复1只是具体提议，未由原组件登记新授权；正式统计合同/V4/隔离样本资格也没有随取数通过。STRICT_TRAIN_INPUT_READY、READY_FOR_REAL_TRIAL、R1_FULLY_CLOSED、AUTONOMOUS_STRATEGY_GOAL_COMPLETED继续false。旧结果、未知、历史预算和Windows OPEN保留，未merge/push。

## 工件

READ_PLAN.json、122份symbol.json、ACCESS.jsonl、PROGRESS.json、SUMMARY.json、FULL_LIFECYCLE_COVERAGE.json保存行情与对账。INVALID_PRICE_CLASSIFICATION.json、SUSPENSION_FIELD_SEMANTICS.json记录假设驱动的定因，不回写原初筛。ACTION_DATE_PLAN.json、ACTION_DATE_RESPONSES.jsonl、ACTION_SUMMARY.json、EXTRA_ACTION_DATES.json、ACTION_DISCREPANCY_PLAN.json、ACTION_DISCREPANCY_TERMS.json保存全部日期/条款对照。没有计算新收益、因子、PnL或筛选最好结果。
