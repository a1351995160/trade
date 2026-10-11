# 已知报告故障的一次工程修复

账户回测和逐日核账已经成功、报告进程却失败时，原研究不能直接称为完成。普通入口继续拒绝对已结算失败免费重试。

维护者可以为这个精确失败另行批准一次报告修复。修复重用原账户、原核验、原策略和原数据，在独立目录生成报告；原失败和已消费费用永远保留。一次修复即使再次失败，也不能换编号再试。

## 准备与批准

先用合成数据定位并验证修复，固定新源码。`ReportRepairV1.summary()` 从原公共任务、失败结案、失败计算用途及账户原件构建不含收益的批准摘要。原源码必须在原归档中核对，新执行只接受摘要列明的源码替换和闭合运行文件。

修复使用原 campaign 和原受保护的 Owner 批准库。资源增量仍通过既有 `grant_summary()`、`OwnerApprovalStoreV1` 和 `ResearchCampaignV1.add_grant()` 追加；专门报告修复还须独立批准摘要。原合同、原 SETUP、原账本和源码身份不能原地改写。

输入实现修复也会改变能力版本指纹。`ReportRepairCapabilityBridgeV1` 只接受明确批准的 `universe_account_inputs_v1.py` 单项替换，核对旧归档与新源码，再严格重建原能力指纹并验证原合同。定位记录追加在原 Owner 根，未批准的源码或能力变化仍拒绝。它不授予预算，不扩大策略能力或独立验证范围。

本次已批准边界：全局及 EXPLORATION 的 `verification_jobs` 各增加1（4→5），一次修复最多28800秒；单段仍900秒、2048MiB，累计墙时总额288002秒不变。候选、数据、账户、模型、预测试验及期限不变。

## 公共执行与恢复

`ReportRepairV1.freeze()` 写入一次不可变修复清单，返回其路径和 SHA256。所有后续动作必须携带这两个字段：

```powershell
python scripts/run_report_repair_v1.py advance --manifest <清单路径> --manifest-sha256 <SHA256>
python scripts/run_report_repair_v1.py reconcile --manifest <清单路径> --manifest-sha256 <SHA256>
python scripts/run_report_repair_v1.py verify --manifest <清单路径> --manifest-sha256 <SHA256>
python scripts/run_report_repair_v1.py feedback --manifest <清单路径> --manifest-sha256 <SHA256>
```

`advance` 最多派发一段；`run` 在同一有界用途内连续推进各段。中断后必须先确认原进程退出，并以 `reconcile` 结算原派发；这一步不启动计算。已知失败不能再次派发。所有计算沿用现有受限工作进程、计费账本和 campaign 的 VERIFY 用途，不另造预算。

修复报告、检查点、资源、完成回执及反馈位于原任务的 `report-repairs/<repair_id>/`。原账户结果和结算不复制为新账户，也不改变原核验绑定。旧报告检查点保留，新版本重新聚合报告。

## 完成和接续的含义

`REPAIR_COMPLETION.json` 证明专门批准、原失败、新报告、受限执行和实际收费完整相连。`BusinessValidationProtocolV1.verify_report_repair_refs()` 重新检查两份账户及报告的日期、资金、输入身份和来源。通过后才能生成修复反馈，作为下一批策略设计的依据。

成功工作进程的 RESULT 固定研究报告与信号漏斗，最后汇总的 `FINAL_REPORTS.json` 固定双成本最终报告。完成状态、资源回执及收费链必须共同锚定这些哈希；修改报告后重新计算文件哈希不能获得完成回执。反馈中的策略、输入和阶段也必须与原冻结任务一致。

原候选仍为 `FAILED`，新增事实为 `REPORT_REPAIRED`，不能倒改第一次结果。报告修复完成只证明工程链路完成；策略是否达标、独立252日、正式统计资格和 Paper 仍分别判断。旧历史不得重新贴上独立验证标签。
