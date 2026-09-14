# 现有账户统一接入完成说明 V2

本版本替代 V1 文档中“仅迁移单ETF、未迁移网格/股票、没有公共CLI”的现状描述。项目现有三类账户已接入同一策略计划、原授权预算、受限worker、结算和报告流程；没有运行新的真实回测。

## 已完成的接入

| 账户 | 策略入口 | 共用执行后端 |
| --- | --- | --- |
| 单ETF日线仓位策略 | ETFPolicyStrategy / 自定义 on_close | ETFDailyBackend |
| 核心长期持有＋网格独立资金桶 | ETFGridStrategy / BucketDecision | ETFBucketBackend |
| 多A股因子排序、逐lot退出 | StockFactorStrategy / build_rules | StockAccountBackend，调用原 _run_account |

参数、Requirements、规则源码及依赖、实现模式、输入身份、Provider适配和账户能力共同冻结。支持现有规则的新增候选主要增加配置；新规则可增加插件。新增订单语义或市场必须实现对应后端能力，不能声称任意策略都不用写代码。

策略只提供决策，账户侧保留费用、滑点、容量、订单、T+1/FIFO、公司行动和账本。网格保持核心5000、网格4000、永久现金1000，不出售核心补网格；买入持有使用9000桶。股票保留原Top3、100股整手和公司行动hazard规则。资金桶订单先整批检查再提交；插件不能改变资金桶上限。原始成交与独立因子视图通过输入合同隔离。

## 唯一公共任务入口

`scripts/run_strategy_account_v1.py` 提供以下命令（路径为待填占位，不构成执行许可）：

```powershell
.venv/Scripts/python.exe scripts/run_strategy_account_v1.py --prepare CONFIG.json --root NEW_JOB_ROOT
.venv/Scripts/python.exe scripts/run_strategy_account_v1.py --execute --job NEW_JOB_ROOT/JOB.json
.venv/Scripts/python.exe scripts/run_strategy_account_v1.py --status --job NEW_JOB_ROOT/JOB.json
```

配置集中声明 objective_id、原权威 budget_path、input_identity、items，以及可选 benchmark_id。每个item指定本地可信 factory、factory_kwargs、loader、loader_kwargs、可选backend_options和dependency_files。factory与loader采用 `module:symbol`；不接受未审阅外部Python代码。prepare不读取行情、不授予预算，冻结JOB和源码归档。具体批准由原 StrategyBatchGovernanceV1 接收，必须绑定全部计划身份及实际新颖性结果；不能用配置或本次工程批准代替真实执行回执。

公共loader已接入现有冻结ETF快照及股票Provider：`load_etf_snapshot` 验证快照、日历和actions文件身份；`load_stock_provider_snapshot` 调用原 `prepare_baostock_account_v1.load_bundle`，验证基础清单、特征清单、特征文件及合同血缘。没有重新下载已核验数据。本轮未对真实输入调用这些loader。

execute复用原Windows受限worker，并发1、数值线程1、单worker900秒/2048MiB；累计上限取4500秒和计划数×900秒较小值，更早授权期限优先。计算前登记消费，所有结束路径写资源与结算；失败保留具体异常，不能自动退款或重试。无合法回执、撤销、到期、源码变化、输入不符、已尝试过均拒绝。`--worker` 是内部握手入口。

status给出任务数、结算数、百分比和失败/未结算状态；未结算不冒充仍在运行。输出包括原生完整账户结果、统一JSON/中文报告、结果索引和报告访问记录。收益、回撤、费用、交易及ledger保留引用；部分失败保持空指标，不伪装成功。有指定基准时只在完整日期序列一致且结果完整时显示对照，不事后选择交集。

历史专用脚本及冻结源码保留用于追溯，不再要求新增候选复制它们。现有单ETF日线与多A股账户已经迁移；分钟、期货、其他交易制度不是本项目已有能力，不作已支持承诺。

## 验证与证据

- 相关九个测试文件：86项通过（21.59秒）。随后公共报告、进度及失败处理补充后的迁移专项：9项通过（12.43秒），与前者有重叠。
- 真实受限worker执行合成网格和股票账户，验证冻结、消费、执行、结算、索引、无回执拒绝、重复执行拒绝及失败不免费重试。
- 本轮改造前备份与当前网格、买入持有、多股票的完整合成输出SHA256分别一致，见 `artifacts/strategy-interface-migration-v2/PARITY.json`。原接口测试还保留五个ETF规则的旧版完整合成输出基线；不宣称对任意输入的形式等价。
- 两个历史授权测试最初因真实时钟越过旧到期日失败；只将这两个测试时钟固定到原授权区间，生产授权期限未修改。
- 旧结果与归档按既有清单只读校验hash，记录于 `HISTORICAL_HASH_CHECK.json`。不重跑旧回测、不消费真实预算、不启用V4；Windows OPEN事件及正式三个false标志不变。

## 恢复本轮改造

本轮前源码副本及hash在 `artifacts/strategy-interface-migration-v2/before`、`BEFORE.json`。确认没有后续编辑后，可对需要恢复的同名文件逐个使用 PowerShell `Copy-Item -LiteralPath <备份文件> -Destination <对应源码文件>`；不git reset、不整目录覆盖。

移除本轮新增 stock_strategy_v1.py、公共runner及迁移专项测试前应确认无新调用方；ETFPolicyStrategy仅撤销本轮新增validate方法，历史测试只撤销本轮historical_grant_clock改动。保留上轮公共接口、其他会话改动、真实数据/结果/预算与历史记录。
