# Trusted Research Acceptance

本文件记录第一阶段真实验收准备，不表示已经通过。设计、配置和原件清单位于 `reports/trusted_workflow_acceptance/`。

## Frozen Engineering Scope

- 预热从 2022-04-07 开始，评价期固定 2022-07-06 至 2022-12-30。该窗口只验产品链路，不判断策略有效。
- A 池：000001.SZ、000002.SZ、600000.SH。B 池：000006.SZ、000733.SZ、002036.SZ；取自旧事前冻结股票池的前三只，未查看收益作选择。
- 三份规则：MA10 固定成本止损、MA20 多条件固定止盈、双均线移动止损。同样规则在两池分别使用 50,000 元，正常成本、压力成本及全池买持基准各独立执行，共18作业。
- 补充 A 池第一规则的 100,000 元版本，再加3作业，用于验证资金变化不是收益简单比例缩放。所有作业仍需正式冻结、用途预算与当前授权绑定。
- 数据或股票不支持时记录失败，不替换成盈利股票，不调整门槛凑退出触发。

## Data Inventory

A 池原件：`E:/llmwiki/autonomous-strategy-research-v1/autonomous-completion-20260927/THREE_SYMBOL_DATA`。仅使用 TRADE_DATES、3份 DAILY、3份 ADJUST 和3份2022年度 DIVIDEND。`manifest_A.json` 记录真实字节哈希与整文件日期范围；未修改原件。日线文件覆盖2022-01-01至2024-07-31，因此即使验收止于2022年，内容授权也须覆盖整个原件。

旧全市场 BaoStock 库 `execution-data-v1/baostock-account-v1/responses/<symbol>/3.json` 的请求元信息显示：日期从2022-07-22开始，字段不含换手率，也没有配套真实股息日期条款。主目录旧12股研究的实际来源是 `data/research/daily_all.parquet`、`data/cache/gbbq.csv` 和证券状态目录；其loader明确推算登记日、支付日与发布时间，并使用换手率占位。因此不能作为新可信验收的直接输入，也未读取含封存区间的大行情文件。

B 池需要9个固定BaoStock补采请求：3份日线、3份2022年度股息、3份复权事件覆盖。通用 `scripts/fetch_research_baostock_v1.py` 已按清单尝试连接，登录超时，9个行情请求均未发出。真实错误保存在 `sources_B/LOGIN.json` 与 `ACQUISITION_RESULT.json`；没有伪造数据或自动重试。B池清单尚不能生成。

## Alternative Provider Metadata

已只读核对 `C:/Users/84219/.codex/skills/tdx-tq-local/SKILL.md`。`get_divid_factors` 文档提供除息日、每10股分红、送配股字段；`get_gpjy_value` 的 GP41 提到股权登记日／配股登记日。但未找到实际现金支付日字段。仅凭这些说明不足以满足当前账户所需完整股息条款，不能把除息日默认为支付日。本次未调用行情或交易接口、未下载或启动客户端。

## Acceptance Gates

1. 公共源码稳定后才冻结任务；使用公共能力查询、公共提交与原预算治理，不调用旧候选专用装载器。
2. 每作业检查原件资格、来源与授权绑定、实际成交、逐日独立核账、退出行为与报告一致性。
3. 六组50,000元全部完成才能宣告两池验收通过；B池缺数据时本阶段仍未完成。
4. 三类退出在确定性测试必须实际触发；真实数据不触发时如实记录，不改参数挑选窗口。
5. 中断恢复须逐日账户和待退出行为一致；关闭AI后仍能运行。进程测试通过不替代真实原件验收。
6. 核账通过、历史初筛通过、独立验证通过和正式资格分别列示；本验收不授予Paper资格，不产生真实观察天数。

## Current Evidence

当前状态：A池已由公共 ResearchDataProviderV1.prepare 完成真实原件资格检查，证据为 QUALIFICATION_A.json 与逐次落盘的 DATA_ACCESS_A.jsonl；仅表示探索账户数据可用。B池补采登录失败；未冻结账户、未创建账户批准回执、未消耗账户执行预算、未运行任何本轮真实策略。

回滚：移除本轮新增配置/说明及采集脚本即可；保留失败原始凭证便于核对，原行情、旧研究和运行中AI目录未修改。

## 2026-09-29 Data Qualification

A池输入身份：`355cd3be7e4b519222eed14180e7ea82c7005773b2e465534fd90e1b02ffd754`。整文件授权范围2022-01-01至2024-07-31，实际工程窗口未改变。数据可用性为MODELED，独立性UNKNOWN，不取得独立验证或正式策略资格。

TQ技能前置检查确认Windows和量化模拟版安装；TdxW进程未运行，故未发送HTTP请求。BaoStock 0.9.4仅配置公共和VIP服务器；VIP路径要求API key，不能当作匿名备用。详见ALTERNATIVE_PROVIDER_CHECK.json。

数据提供器单测：`python -m pytest tests/research_factory/test_research_data_provider_v1.py -q -p no:cacheprovider --basetemp=.test-tmp/provider-qualification-20260929`，18 passed。首次默认临时目录运行在会话清理时出现WinError 5，改专用临时目录后退出码0。

全E盘只读文件名扫描（含隐藏及被忽略文件，不读文件内容）未找到B三股DIVIDEND/ADJUST原件；只发现A池、旧2股数据与合成测试夹具。B三代码精确文件名检查亦无匹配。该检查不能证明任意命名文件不存在，但没有新增可验证来源，故B与两池真实验收仍阻塞，S1未完成。

## 2026-09-29 B池补采成功后的数据准备

后续同一授权下连接恢复，9个固定请求全部成功，新原件保存在 `reports/trusted_workflow_acceptance/sources_B_connection_check_2`；原失败目录与凭证保留。日历从A池原件按字节复制，SHA256为 `87f146a3e741deadb598eefaadfe8cc0c09ec590be39e0ac9ee5dd94bd691f4c`，未重新请求或改写日历。

`manifest_B.json`记录10份原件的整文件身份；股息文件覆盖按实际日期字段取最早与最晚：000006.SZ为2022-04-16至2022-06-21，000733.SZ为2022-04-29至2022-07-14，002036.SZ为2022-04-26至2022-07-11。三股均为现金分红，原件提供登记日、除息日及支付日，支付日与除息日相同，无送转股；适配器已核对复权事件覆盖及全部前收价变化，不推算这些日期。

公共 `ResearchDataProviderV1.prepare` 在原定窗口通过，见 `QUALIFICATION_B.json` 与 `DATA_ACCESS_B.jsonl`，输入身份为 `a74c880706b0a5260e2fca973b298526d032537a6893bb45f74b8d685efaa6ff`。历史可见性仍为MODELED，独立性UNKNOWN，只取得探索账户数据可用结论。

`public_run`新增B池三份REQUEST与对应AUTHORITY，每组50,000元、BASE/STRESS/全池基准共3作业；DEPLOYMENT追加B数据与授权登记。公共preview及授权内容身份核对通过。A池请求与授权文件字节未变化。本次仅准备与读取数据，未冻结B任务、未批准作业回执、未运行账户，因此两池真实账户验收仍未完成。

回滚可移除本次B注册项、B请求/授权和新清单/资格说明，保留全部采集原件及旧失败凭证；无需更改A账户、既有预算或源码。

## 2026-09-29 最终S1真实账户与离线复核

两池三规则各50,000元，加A池MA10的100,000元对照，七组公共任务的BASE、STRESS、全池买持基准共21账户全部完成并核验。每账户覆盖原定122个评价交易日，合计2,562个账户日；这不是2,562个独立交易日或Paper观察日。真实结果摘要为 `reports/trusted_workflow_acceptance/REAL_ACCOUNT_SUMMARY.json`，版本与证据绑定发布包为 `PUBLISHED_ACCEPTANCE.json`。

额外离线复核 `REAL_REAUDIT.json` 对21个原JOB重新调用 `verify_job_evidence`，全部PASS并记录JOB、plan、result、settlement身份；复核前后所有账户JSON与预算文件哈希保持一致，没有启动新账户或新增预算消费。A池MA10五万元BASE的真实冻结输入与结果仅在内存复制后，将首日cash加1元，独立重建以 `DAILY_CASH_CONFLICT` 拒绝；任何原件均未修改。

三类退出在此次真实数据中均有实际触发评估：固定成本止损20次、固定止盈14次、收盘高点移动止损12次（合并各资金及成本账户，非独立事件数量）。这些账户均通过包含实际退出订单、成交／客观阻断及FIFO归属的独立核验。资金对照也非简单倍数：MA10正常成本下，五万元费用249.2483元、十万元491.3653元；净收益率分别11.6043834%与11.7668747%，均26笔成交。这仅用于证实整手、费用和资金约束下执行行为的差异，不能据此选择资金规模或承诺收益。

A池MA10五万元的 `RECOVERY_CHECK.json` 报告结果、结算与预算字节一致；本次再次逐一核对其7个文件哈希，仍全部吻合。`BOUNDARY_PROCESS_WITH_ENV.json`记录进程在账户计算完成、核验及报告尚未生成的边界正常退出（returncode 0），之后恢复完成且未重做账户。该证据是阶段边界恢复，不冒称随机强杀或所有故障点都已经真实演练。

最终S1结论：既定两池、三类规则、实际资金、成本／基准账户、退出与离线核账的工程验收通过。历史可见时点仍为MODELED，历史独立性UNKNOWN；本验收未证明策略有效、未通过独立验证、未授予正式策略或组合资格，也未产生真实Paper观察天数。后续方法与真实资格应按各自证据独立判断。旧B池连接失败凭证及后续成功原件均保留，时间线不改写。
