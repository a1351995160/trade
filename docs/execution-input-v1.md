# 首个交易级训练回测：已实现的输入工程与明确边界

本轮沿原任务实施，不改变历史预算、正式政策或统计资格。交付根为 `E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/`，实际交付状态以该目录的报告、manifest及INPUT_READINESS为准。

新增 `ExecutionTrainingAdapterV1` 将日线模型可见时间设为上海时间收盘后1微秒，晚于模型的已有证据优先；原历史时间接口不填造时间，单位未核验时不提供成交Bar。该适配器尚未接入实际训练runner，不能将其测试通过解释为回测入口已就绪。状态保留正常、ST、停牌、非成员及未知/冲突的区别。

本机日历响应使用Date字段。旧新适配草稿误读Value导致空日历，已用真实响应形状构造失败测试并修复。公司行动get_divid_factors虽收到TRAIN参数却返回跨1990—2026事件；新适配明确拒绝再次调用该已知不隔离窗口的接口，保留实际越界信息访问记录，不以事后过滤或gbbq整读绕过。

预热reader沿用原TRAIN reader的定长日期键定位算法，仅读取日历指定六个session的完整记录；区间外只读取定位用日期键。旧TRAIN caller窗口检查不变。脚本按冻结清单重用GuardedResearchReader、真实因子registry与FactorCompiler，因子输入补齐独立日历，完整六个正且有限收盘价才可计算。空片段合并产生object价格列的实际错误已最小修复为数值类型转换，不填零、不修改因子参数。每个取得阶段立即保存审计。

本机原文件身份变化时保留V1拒绝证据；V2冻结同一5036路径的新元数据身份，不扩搜146个缺源；V2在因子准备阶段失败，其已写日线和状态保留。V3用于修复后重新物化，不能将新审计冒充V2丢失的运行审计。输入准备均未计算策略收益。

## 使用与恢复

只在确认原清单身份仍有效、输出目录不存在时调用：

```powershell
$env:PYTHONPATH='E:/llmwiki/bounded-offline-strategy-research-v1/src;E:/llmwiki/bounded-offline-strategy-research-v1/scripts'
$env:OMP_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
.venv/Scripts/python.exe -c "from prepare_execution_input_v1 import prepare; prepare('INPUT_READ_PLAN_V2.json','materialized-v3')"
```

已存在目录会拒绝重写。此命令只做输入工程，不启动回测或登记预算；不要作为日常重复任务执行。精确输出与来源核验在交付manifest中。公司行动所有者导出由 `normalize_windowed_actions` 做字段、身份及窗口验收；调用方必须先取得上游物理窗口化保证，该函数不是混合文件读取授权。

## 仍需的核心决定

现有CorporateActionGuard只标记TRADE_REALITY_UNSUPPORTED，没有现金、股数或配股账务处理。所需事件字段与确定型反例在外部决定包；核心会计新增语义需明确决定，不能用RAW/qfq标签切换替代。新增2次用途批准尚未转为可执行预算回执；旧12消费、4探索和旧2修复额度保持。数据/会计通过后再由原权威服务完成本用途增量与执行合同，不再次请求相同用途批准。

Windows OPEN、旧失败、V4未批准和三个正式false标志保留。
