---
title: 原FAILED账户一次性生产接续与门禁后恢复
type: fix
status: active
date: 2026-10-16
---

# 目标与固定边界

按2026-10-16本线程明确人类授权，完成生产恢复链接入、启动边界验证、精确CI和完整合成；
全部门禁通过后直接合并部署、原Owner限时安装，从原任务第278日续跑。
原型树和79e9旧优化树保留，不作为已部署生产功能。

- 原BASE277/504、STRESS0；原段50 FAILED和原SETTLEMENT/Trial终态保持。
- 原canonical42339.896402100036秒、父used42340秒，不退款、清零或改写。
- 原两账户各72000秒、global288002、原期限2026-10-17T19:43:08.066473+08:00不变。
- 仅失败BASE的未用整数预留29660可按原子父事件恢复；STRESS原72000预留不增加。
- 不新增ACCOUNT、候选、用途、grant或准备额度；不重跑已提交277日，下一段51。
- 原FAILED作为历史终态，接续有独立执行状态、证明和结算；再次失败即停止。
- 仅治理闭包变化，99经济库存及持仓/策略不变；完整fresh Owner/源码/日文件/侧车核验保留。
- 未过全部门禁不真实派发、不安装许可、不修改main；到期或预算不足真实停止，不自动扩限。

## 共用接口决策

新生产模块`failed_account_recovery_v1.py`承载受Owner签发的独立失败接续证明。
采用与既有time-extension一致的summary/freeze/read/from_job模式；generic setup不持签发能力。
`FailedAccountRecoveryV1.summary(...)`绑定原TASK/JOB、原time-extension manifest、原50段失败
RESOURCE/CHARGE/SETTLEMENT、277日CP、原费用/控制/会话前缀和最终治理/经济库存及CI证据。
summary/freeze不派发；父install、子mirror repair/strict、公共dispatch分调用。

新父事件`CAMPAIGN_FAILED_ACCOUNT_RECOVERY_INSTALLED`只追加一次：
保留原operation.status/actual/Trial完成事实，另投影recovery状态和新收费；
通过`effective_execution_status(operation)`显式供新路径使用，不放宽普通FAILED拒绝。
原segments50项保持精确前缀，新段连续51起；canonical继续累计，父used采用累计ceil差额。
STRESS只继承治理源码与原有效72000权限，不增加预留或一次失败重试机会。

新源码必须通过明确受Owner批准的继承链读取旧manifest与档案；
不可通过通用historical=True或任意dict跳过当前源码校验，读取路径也须核新证明真实性。
纯manifest resolver不得调用campaign/账户执行API，避免重放循环和隐式写入。

worker启动预算以真实资源握手/worker入口绝对monotonic截止为准；
所有启动检查、loader及首次guard包含在同一预算。完整校验未完成不能进入经济计算。
阶段上界未知不可假装已可中断；真实supervisor timeout仍FAILED并付费。
使用人工输入验证耗尽/临界/硬超时、Windows限额和完整真实worker链，不免费读取真实价格预跑。

## 实现单元

### U1 — 新Owner失败接续能力与来源继承

Goal: 对原已失败用途的一次性接续签发与读取完整绑定，新许可只覆盖最终实际治理字节。

Files:
- Create `src/chanlun_trader/research_factory/failed_account_recovery_v1.py`
- Modify `src/chanlun_trader/research_factory/campaign_scope_v1.py`（仅新能力审批语义分支）
- Create `tests/research_factory/test_failed_account_recovery_capability_v1.py`

Approach: 复用原Owner对象能力、受限文件引用/ACL/不可变slot；原旧manifest和source archive
独立核验，新继承链必须证明归属和确切旧/新SHA。新read只读、不签发、不写状态。
原始FAILED与checkpoint绑定允许后续日只追加，不接受缺尾或身份漂移。

Test scenarios: 合法签发/重复freeze幂等、无能力拒绝、任意hist绕过拒绝、篡改原失败/费用/CP/
源/日期/经济库存拒绝、过期撤销拒绝新派发、旧原件读取保持、原未启动STRESS不变。

Verification: 精确新接口单元/受Owner人工完整fixture通过；独立复审来源继承不形成循环或旁路。

### U2 — 原父预算的一次性接续事件

Goal: 原FAILED/actual/Trial终态不重开，用独立恢复投影原子恢复29660整数预留并严格计费。

Files:
- Modify `src/chanlun_trader/research_factory/run_budget.py`
- Modify `src/chanlun_trader/research_factory/research_campaign_v1.py`
- Create `tests/research_factory/test_failed_account_recovery_budget_v1.py`

Approach: 专用安装事件和effective_execution_status；原总账锁内fresh重放、真实headroom、
原事件前缀/失败证明匹配；原segment50的FAILED不改标，新段51及结算绑定dispatch身份。
原used不可退回；canonical与ceil增量不可双取整；新恢复失败或完成释放其剩余预留。
旧Trial完成记录和计数保持，新恢复不新建Trial/ACCOUNT/候选/grant。

Test scenarios: 正常安装与51计费、小数进位/临界72000、并发安装/其他用途竞争、重复结算/
迟到回调、active/UNKNOWN/已再次失败拒绝、原ERROR/FAILED/费用前缀保持、撤销到期后的费用结清。

Verification: 原预算回归及新父/子契约通过；全局used/reserved/remaining与真实累计一致。

### U3 — worker启动边界与账户执行路由

Goal: 在原公共worker链中完成完整校验后才计算；使用绝对启动预算，原失败保留，新接续独立结算。

Files:
- Modify `scripts/run_strategy_account_v1.py`
- Create `src/chanlun_trader/account_startup_boundary_v1.py`（如确需轻量共享预算模块）
- Create `tests/research_factory/test_failed_account_recovery_worker_v1.py`

Approach: 对显式context.recovery路径新增分支；原路径保持。原BASE失败SETTLEMENT不覆盖，
新接续START/SETTLEMENT独立文件；report读取严格经过新验证结算，旧FAILED普通入口仍拒绝。
启动边界验证必须包含loader与首次guard，不仅在最后检查。硬超时仍记录FAILED，不改标或免费重试。

Test scenarios: 人工真实worker启动早/晚/耗尽、未校验完不进入引擎、段51从278提交、原失败
SETTLEMENT不动、继续/完成/二次失败、宿主timeout/内存失败和侧车proof一致。

Verification: Windows/跨平台实际worker链与公共入口回归；最终完整两成本504及原生对照通过。

### U4 — 上下文、子镜像与公共会话集成

Goal: 新能力贯穿原55pins/ACL会话、版本上下文、治理子镜像、submission与合法反馈路径。

Files:
- Modify `src/chanlun_trader/research_factory/account_version_continuation_v1.py`
- Modify `src/chanlun_trader/research_factory/account_purpose_time_extension_v1.py`
- Modify `src/chanlun_trader/research_factory/etf_account_governance_v1.py`
- Modify `src/chanlun_trader/research_factory/continuous_submission_v1.py`
- Modify `scripts/run_account_version_session_resume_v1.py`
- Create `scripts/setup_failed_account_recovery_v1.py`
- Create `tests/research_factory/test_failed_account_recovery_integration_v1.py`
- Create/modify相应限定测试与docs/research生产契约说明。

Approach: context.recovery为已完整验证的U1对象；原time-extension仅通过明确新继承能力读取旧字节。
gov.segment_status/active_execution/record_dispatch/charge映射U2独立恢复投影并检查相同proof。
原immutable ERROR保留，只有绑定原失败和CP的新证明允许公共advance继续；普通失败不放行。
专门repair→strict→WAITING零派发，之后独立公共有界会话；原55pins/ACL真实验证保留。

Test scenarios: 公共同JOB51/278接续、原日/费用/FAIL保持、缺镜像专门repair且本次零派发、
被撤销/篡改/过期/迟到回调阻断、完整504+核账+report/PUBLIC_RESULT/合法FEEDBACK幂等。

Verification: 人工合法生命周期的真实公共执行链通过；fixture明确标注的装配替身不代替真实Owner安装。

### U5 — 精确门禁与工程交付

Goal: 最终生产字节的本机必要回归、CI、完整合成和独立复审齐全后合并部署。

Files: 本计划与新增生产文档/progress；精确新head下测试、CI及外部合成证据。

Approach: 固定最终source集合后运行完整合成与CI，期间不改被测字节；失败原件保留，修复后
重新绑定新head/源。独立审查高风险状态、预算、Owner和启动边界；不复用旧R6/71CI代新字节。
PR42不混入；合并立即前后核main progress与原旧计划，保留用户未提交资料和旧优化。

Verification: 最终head精确CI全SUCCESS、完整两504/核账/原生对照/公共反馈自然PASS，独立findings=[]。

### U6 — 原Owner有限安装与原真实账户恢复

Goal: 全门禁通过后安装一次限时接续证明，实际提交原第278日并继续原目标。

Files: 原Owner受信安装对象能力调用、原真实TASK下新独立恢复工件、四未冻结状态/docs/progress。

Approach: fresh原Owner canonical对象/ACL、最终main实际治理字节、原277CP/50费/无active/UNKNOWN；
原token只经对象能力使用，不取文件/不写日志。freeze只证明，父原子install后专门镜像repair/strict
并WAITING；独立原公共短会话从278日起，原FAILED/费用/日不重跑。到期/额度不足/新FAILED停止。

Verification: 新51派发与worker/input/version/RESOURCE/CHARGE proof一致、278日及277前缀实际审计；
母子费用严格一致，原canonical继续累计，原预算/期限不变。随后原有限会话持续至真实合法结果。

## 并行实施安全边界

U1/U2/U3文件集合无交集，允许按ce-work Phase1 Step4并行；U4/U5/U6由主代理负责。
各实现者只改本单元文件，不stage/commit、不跑共享测试；主代理待字节稳定后统一验证/提交。
任何接口调整先协调，不能静默放宽Owner、来源、失败或费用断言。计划正文保持决策，实际进度
单独在生产tracker和progress追加，不改写原2026-10-10计划。
