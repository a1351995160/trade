# 原FAILED账户一次性生产接续

本接口用于本次已明确批准的原账户失败后接续。原FAILED、原Trial终态、原50段收费和277日
经济状态保留；接续使用独立Owner证明、执行状态、START和SETTLEMENT。它不是普通FAILED重试入口。

`FailedAccountRecoveryV1.summary/freeze/read`绑定原任务、扩时前驱、失败与费用前缀、277日日链、
控制head、最终治理源码和不变经济库存。签发仍要求原Owner对象能力。通用
`scripts/setup_failed_account_recovery_v1.py`的`plan/freeze/inspect`只规划、消费已有批准或核验，
不签发Owner能力、不安装、不预留、不派发。

父`CAMPAIGN_FAILED_ACCOUNT_RECOVERY_INSTALLED`事件只允许安装一次。原operation.status和actual
保持FAILED与42340整数秒，另投影接续状态；仅恢复原BASE未用29660整数预留。STRESS原72000
预留保持，继承新治理源码，不取得失败重试资格。后续收费按原canonical累计的ceil差额登记，
不得清零、退款或重复向Trial计费。再次失败、余额不足、撤销或原期限届满停止派发。

安装、镜像修复和派发分调用执行。缺`ACCOUNT_FAILED_RECOVERY.json`时，专门reconcile后strict核验，
返回WAITING并保持零派发。之后独立公共会话才从段51/日278推进。批准只消费被冻结的历史暂停
head；之后的新PAUSE仍然阻断。新段所有proof、dispatch_identity与母子费用必须一致。

原BASE的失败START/SETTLEMENT/RESOURCE等文件不覆盖；新接续采用`_RECOVERY_*`工件。
报告绑定严格验证的新接续SETTLEMENT。独立经济核账直接调用既有核账实现，新治理适配器只负责
来源、旧失败前缀和新执行证据，不新增策略核账算法。

worker以资源握手或实际入口的绝对截止为预算起点。域导入、Owner/源码/日工件核验、loader、
准备和首次guard都在同一启动边界中；完整核验完成后才进入冻结经济实现。可中断的Python启动
工作和分块读取会检查截止，完成后撤除启动trace，避免在日内引入新中断点。未知native调用仍可能
耗尽supervisor限额；此时真实FAILED并付费，不能把协作边界或人工测试当作native耗时上界。

部署必须绑定最终实际字节的精确CI、完整人工双504/277日前缀/原生连续对照/报告和合法公共反馈，
并完成独立审查。人工旧费用producer、人工Owner和会话装配须明确标注，不能代替真实费用或真实
55pins/ACL安装验证。旧R6结果和隔离原型结果不作为本功能门禁。

安装前可回滚到main `1b787901af43a77b4f150bd32e98ac96b5e9171a`。安装后不逆写原子事件或退款；
发生新失败、来源冲突或预算异常时保留现场并停止派发，按既有核验与结清边界处理。
