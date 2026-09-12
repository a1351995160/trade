# 一年外窗批准后的实际执行

用户已批准本窗口可行性门槛为30条完整候选路径、10个开仓日、至少2只证券。原TRAIN门槛不变；不是正式统计资格，也不是新增策略。原窗口释放回执和本次门槛批准分别保存，旧方案不改写。

## 已完成工程

版本化窗口仅接受完整固定月末候选合同，默认TRAIN及Final Test守卫不变。RAW输入进入原账户成交仓库；HFQ仅用于原月末信号。仅外窗日期计入窗口账户指标；预热不生成窗口前交易。

已实现独立日历、逐日历史成员、证券生命周期、RAW/HFQ与调整日期的可校验归档；缺失状态保持UNKNOWN、冲突明确拒绝，不能以当前状态填造历史PIT。原双价格校验、原技术特征、原无收益路径检查和原账户组件被复用。调整日期使用hazard拒绝，不能视为完整公司行动条款。

授权通过原父回执、原Objective和SearchBudgetRegistry登记1主+1确证工程修复。只有实际输入及可行性通过才登记；没有另建预算账本。执行之前冻结源码与合同，失败不自动重跑。原引擎的train命名指标字段在新报告明确说明为获准外窗口径。

## 正在运行

私有证据：`E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/monthly-independent-window-v1`。

取数进程18740（实际解释器子进程6760）已启动；接续修订进程4644等待其退出，再检查全部批次回执。只有输入齐备时才执行物化和门槛检查，通过后接续一次主账户验证。等待进程不发起第二个取数worker。取数失败或资源/期限耗尽时，后续检查拒绝，错误归档；没有自动重试。

初版接续进程23668的Wait-Process提前返回，触发ACQUISITION_NOT_COMPLETE拒绝；当时18740仍在运行，未进入物化、额度或账户。原等待的具体OS错误因SilentlyContinue未保存，不能假称已经还原。现改为实际进程清单仍含目标PID时继续等待；新进程实际保持等待、stderr为空、尚无完成回执。旧PIPELINE_STARTED/COMPLETED.json、stdout/stderr、CONTINUATION_SOURCE_V1.ps1、EXECUTION_SOURCE_V1.py、EXECUTION_FREEZE.json保留；修订使用PIPELINE_STARTED_V2/COMPLETED_V2及EXECUTION_FREEZE_V2，不覆盖旧证据，不扣绩效修复额度。

历史股票池接口单日响应约二三十秒。初始整批实现不适合每worker900秒限制，已保留FETCH_SOURCE_V1.py及FETCH_CODE.json，利用源码冻结检查在完整响应之间停止旧worker，原退出1/FETCH_SOURCE_CHANGED回执保留。V2按10日期一批，复用完成且哈希一致的原响应；旧耗时137.527秒仍计入数据资源。不是绩效修复，不消耗账户修复额度。

数据资源180分钟、账户资源90分钟；每worker900秒/2048MiB、数值线程1、并发1；截止2026-09-14T10:05:03+08:00或更早父期限。数据尚未全部就绪，不能预称回测已完成。

## 查看状态与交付

循环查看：

```powershell
powershell -NoProfile -File E:/llmwiki/bounded-offline-strategy-research-v1/scripts/watch_monthly_window_v1.ps1
```

加`-Once`只检查一次。Ctrl+C仅停止查看，不终止任务。脚本不读取绩效、不补额度、不重试。

完成后核对PIPELINE_COMPLETED_V2.json、READY.json、FEASIBILITY.json及ACCOUNT_SETTLEMENT.json；如账户实际执行，输出ACCOUNT_RESULT.json完整账本与ACCOUNT_REPORT.md用户报告、ACCOUNT_REPORT_INDEX.json哈希和ACCOUNT_ACCESS.json访问记录。路径当前只是预定交付位置，只有实际文件生成才算交付。

原失败、历史消费及曝光、Windows OPEN保持；不公开推送数据。READY_FOR_REAL_TRIAL、R1_FULLY_CLOSED、AUTONOMOUS_STRATEGY_GOAL_COMPLETED继续false。

## 验证

71项相关测试通过：默认封存、窗口边界、RAW/HFQ分离、原预览/回测一致性、固定日期买入与原20-session退出、费用记账、历史预算不变及不重复执行、未知状态、合成输入物化到外窗可行性。未跑整套CI，未扩大skip、timeout或隔离白名单。循环查看脚本使用UTF-8 BOM兼容Windows PowerShell 5；实际-Once退出0。接续V2实际等待验证通过，执行源码已冻结；真实输入尚在取数。
