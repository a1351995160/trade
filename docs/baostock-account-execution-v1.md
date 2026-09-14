# BaoStock固定账户执行记录 V1

用户已批准具体确认包V2及向前新增主1/确证修复1。本轮从5596d90d913bb26baf0a141f977b3783732f0bea承接；批准事实、原包哈希和任务身份保存在外部输出根的APPROVAL_FACT.json。旧预算消费、原截止时间及Windows OPEN保留。

输出根：`E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/baostock-account-v1`。

## 已确证并修复的输入接线问题

上一轮将adjustflag=2误写为后复权，且合成夹具复制了同一错误。提供者发布示例明确2为前复权；后复权应使用1。独立测试先出现两项失败，再修正实现与夹具。错误前复权响应61份和中断请求1项保留，不进入研究输入；已成功的62份RAW响应验证哈希和查询参数后复用。未重写原确认包，ENCODING_CORRECTION.json追加纠错事实。[提供者示例](https://pypi.org/project/baostock/)

第三批实际发生4个行身份冲突：001239.SZ/2023-12-12、001326.SZ/2023-11-09、001376.SZ/2023-11-03和2023-11-06。请求1却返回行标志3，客户端没有改写。原失败保留在quality/batch-2.json。

定点核验三个证券的TRAIN复权因子，均有初始累计因子1；同时用独立历史上市状态、原始close/preclose和原HFQ响应逐项验证。官方算法定义上市首日因子为1，随后因子由上一日收盘价与当日前收递推。因此只接受：有窗口内独立上市状态转变、从上市日起的初始flag3前缀、逐日价格等于RAW、后续每日前收与上一收盘精确相同的行。原flag3不改写，非初始行、非单位因子、缺上市证据或价格不等仍拒绝。[官方算法](https://www.baostock.com/helpdocs/pdf/BaoStock复权因子简介.pdf)

这是依据文档公式证明价格数值恒等的兼容适配，不是提供者对异常行标签原因的正式解释。证据级别为DERIVED_FROM_DOCUMENTED_PRICE_ADJUSTMENT_ALGORITHM，提供者标签原因仍UNKNOWN。HFQ_IPO_PREFIX_RULE_V1.json、定点接口响应、PROVIDER_FLAG_CONFLICT_TRIAGE.json及QUALITY_RECONCILIATION.json保留全过程；没有调整策略或读取收益。官方PDF的本机下载发生SSL EOF，未绕过TLS；保存的是web工具读取的准确摘录及其哈希，未伪称完整PDF快照。

## 执行约束与恢复

三个入口分别负责受限获取、输入及无收益准备、权威增量与账户执行。均复用原Provider、Windows资源限制器、账户和治理组件。已经成功的请求按原哈希和查询身份复用，中断请求不自动重试；质量失败只有匹配具体纠错证据才能恢复。不得从旧1443路径复制新策略可行性。

只有全池输入、真实新颖性比较和新路径门槛通过后，execute_baostock_account_v1.py才登记增量并执行主回测。完整真实输入当前仍在获取；不得将本轮合成通过当作INPUT_READY。账户结果只在实际写入后引用，不生成占位绩效。

38项针对性测试通过，包含文件落盘到新路径生成、价格模式隔离、上市初始恒等证明与拒绝分支、原账户和治理回归。原两次映射失败及一次测试夹具缺少coverage字段的失败均保留说明；后者只修夹具，没有修改生产保护。没有重跑整套CI、V4或旧探索。

当前不授予正式研究资格；STRICT_TRAIN_INPUT_READY、READY_FOR_REAL_TRIAL、R1_FULLY_CLOSED、AUTONOMOUS_STRATEGY_GOAL_COMPLETED均false。最终实际获取数量、资源及主/修复消费以本输出根的收尾记录为准。
