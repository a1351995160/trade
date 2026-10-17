---
status: active
---

# 原账户有限日历期限接续

人类明确要求继续全部生产门禁，通过后从原第278日接续；新截止为2026-10-20T19:43:08+08:00。各72000秒、global288002、277日、原50FAILED和全部费用不变。原到期事实保留，不追加grant或新任务、候选、用途、Owner根。

旧29712完整4515仍运行，原WT冻结。本隔离WT从29712创建；新源码必须重新通过对应门禁，旧源码证据只保留为历史。

## 决策

把有限日历接续显式纳入尚未签发/安装的一次FailedRecovery Owner摘要，而不改写任何旧授权、receipt、JOB或manifest。恢复安装仍使用原CAMPAIGN_FAILED_ACCOUNT_RECOVERY_INSTALLED原子候选重放，授予同一恢复用途有限执行截止；旧expires_at用于历史核验，新的execution_expires_at只用于明确绑定的执行。禁止通过全局截止变化放活无关操作。

## U1 Owner与冻结接口

- Goal: 完整Owner摘要绑定可选calendar_deadline_extension，精确三字段previous_expires_at/expires_at/human_authorization_ref；旧摘要不隐式获延期。
- Files: failed_account_recovery_v1.py、setup_failed_account_recovery_v1.py、新test_failed_account_calendar_deadline_owner_v1.py。
- Approach: 保留历史expires_at与冻结前父授权身份；新分支核带时区且严格延长、有限人类记录原件与同campaign/objective/额度；完整read每次复核。freeze重新计算同摘要。resolver仅新分支附execution_expires_at和calendar证明。
- Verification: 缺审批/人类记录篡改/错误原期限或身份/无时区或回退/额度增量/新期限到期均拒绝；旧分支保持。

## U2 父预算原子接续

- Goal: 原恢复事件安装时同时验证有限执行期限，但所有旧费用、操作身份及原授权保持；普通无关到期操作仍拒绝。
- Files: research_campaign_v1.py、run_budget.py及针对预算测试。
- Approach: 完整候选重放通过后只给精确恢复两operation投影execution_expires_at；原authorization expires_at保留。安装、开始、段额度、事件重放使用同一截止和完整恢复proof。
- Verification: 安装幂等/原事件前缀/费用/Trial/额度保持；原截止后恢复对允许、无关操作拒绝；新截止到期、撤销、UNKNOWN、再次FAILED拒绝。

## U3 原执行与交付链

- Goal: 原版本、扩时、receipt、worker、核账、原同task报告和公共会话使用受认证新执行截止；旧凭证原件和历史时点不变。
- Files: account_version_continuation_v1.py、account_purpose_time_extension_v1.py、etf_account_governance_v1.py、failed_account_recovery_evidence_v1.py、必要原报告/会话门；相关测试与完整合成。
- Approach: 只在完整fresh恢复并证明已安装时派生执行截止；不把后置receipt投影当作前置授权。保持启动边界/900秒/2048MiB/并发1/99经济源字节。新交付工具另冻结原件，不改r3。
- Verification: 原截止后各公开入口实际接续、新截止拒绝、零派发repair/strict/WAITING、完整双504/双原生对照/核账/报告/PUBLIC_RESULT/FEEDBACK幂等与精确新CI、最终独立实际工件审查。

## 依赖与协作

U1与U2按上述接口独立施工，U3由主代理集成；同一文件只由声明负责人修改，不在当前4515运行WT写源码，不并发启动测试。最终准确新源门禁通过后，才部署并完成原Owner有限安装与真实278。
