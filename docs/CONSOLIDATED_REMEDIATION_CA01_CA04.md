# 集中审计四项修正

范围锚点：继续总集成分支和 Draft PR #9，统一处理 CA-01 使用资格撤销文件丢失、CA-02 新颖性跨入口降级、CA-03 Paper 提交尾部丢失、CA-04 不同计划误报 CURRENT。先取得实际服务复现或明确阻断，再最小修正；模块小提交，最终固定新 HEAD、整体回归、双平台和一次集中修正包。不合并、不退出 Draft、不运行真实研究。

## 接续和证据

- 被复核 HEAD：ffbff0856f95fdd3206ac70de6a82f3fd1164a16；main：e72fa6ae0ace0dbff6eeac87ae0e09082431d89a。开始时本地、远端分支和 PR HEAD 一致、工作区干净，PR OPEN/Draft，无 auto-merge。
- 外部审计包 SHA256：6c34c5b76ef18e5d4595b535ec166d2c6422044fc955e16838306481359d7af6。报告、源码摘录、探针脚本/结果及完整清单已读取，10项成员哈希验证一致。
- 原审计明确只运行 AST 局部探针，没有重新运行项目 E2E；其源码和测试核验与本轮新增服务复现分别记载。
- 本轮原始证据根：`E:/llmwiki/roadmap-engineering-evidence/ca-remediation-ffbff08`；received保留原附件，不执行附件脚本代替项目测试。旧审计ZIP和历史失败不重写。
- EXTERNAL_CONSOLIDATED_AUDIT=CHANGES_REQUESTED_UNTIL_REVIEWED。本文是工程索引，不是授权。

## 执行清单

| 顺序 | 范围 | 验收与当前状态 |
|---|---|---|
| 1 CA-01 | 新资格提交见证、旧历史只读、消费端 | 实际red后修正；31项使用资格/工作台测试通过，额外真实Paper与撤销锁竞争1项通过；最终新HEAD双平台待验 |
| 2 CA-02 | canonical新颖性协议及旧入口 | 正式新意图边界/旧confirm与recover接受已复现；修正后27项R2/新颖性、5项执行器/批次/旧治理、1项真实v1兼容通过；最终认证待验 |
| 3 CA-03 | Paper已提交尾部见证 | 实际尾部丢失red后修正；25项Paper/工作台和2项损坏API测试通过；最终认证待验 |
| 4 CA-04 | 计划内容当前性和运行代码来源 | 4项实际比较接口red后修正；54项计划/组合/Paper/工作台联合回归通过；最终认证待验 |
| 5 整体 | 同一新HEAD本地、CI、UI/API、审计包 | 待四项完成；含base完整差异和ffbff08增量 |

## CA-01 记录布局及恢复约定

新创建请求使用 `synthetic-test-usage-request-v2`；`SyntheticUsageServiceV1` 保留调用接口，实际请求版本明确进入request_id。每个请求目录新增 `head.json`（`synthetic-test-usage-head-v2`），绑定请求ID、请求证据哈希、revision及请求/确认/撤销证据哈希序列。没有新增全局registry、预算或运行权限。

沿用原不可变记录和 `_atomic_write`，在既有workbench共享互斥内先写request/confirmation/revocation，再原子替换head，之后才返回成功。所有新格式读取逐项验证事件与head完全一致；缺head、缺事件、损坏或事件已写而head未提交均fail closed，不回退旧ACTIVE，不自动生成缺失记录。只读读取不修复。

故障后保留整个合成目录用于诊断；本版本没有自动修复/前滚入口。只能在另行明确的恢复操作中核对完整备份原件，不能靠重算hash或新head把不完整历史变成批准。本次保证覆盖单独事件/提交头丢失，不声称抵御不受限操作者同时重写全部文件。原原子替换语义不泛化为整机掉电认证。

旧v1请求不原地改写、不补head、不重算身份。读取显示LEGACY_REVOKED或LEGACY_UNVERIFIED，不能继续提供计划/Paper资格；需要新版本明确请求和实际确认。旧请求不会被静默迁移。兼容夹具为旧交付包中实际服务生成的三份原字节回执，附来源SHA256，且不拿旧记录作为新测试的合法运行授权。

共同边界：原引擎/策略/预算/统计不变；默认只读、无startup recovery、旧CP预测禁令不变；真实数据NOT_VERIFIED、READY_FOR_REAL_TRIAL=false、R1_FULLY_CLOSED=false；Windows L1/L6和其他已登记失败继续OPEN/原状态。

## CA-02 canonical协议路由

原red通过实际v2 Objective、设计审批、冻结/物化、Structural、来源声明/确认、人工预测授权和新版本confirm形成意图。只删除运行时两个标签时，原边界返回passed=True；部分删除被原检查拒绝。来源增加空白造成版本失效后，原新入口拒绝快照stale，旧候选重建边界仍放行，旧confirm返回STARTED幂等回执、recover返回RECOVERED。此次red的engine/绩效访问均0，不能冒称已经运行绩效绕过。

保留原意图记录布局和哈希，不迁移任何历史。将原磁盘读取分离为私有只读`_read_intents`；运行路径`_load_intents`和候选重建检查持久新颖性协议，旧入口遇新版明确NOVELTY_VERSIONED_ENTRY_REQUIRED。新版本及批次适配复用原启动服务，仍执行原全部门禁。

canonical_novelty_boundary先读持久意图，即使两个标签及start_intent_id都丢失，只要当前候选有新协议意图就拒绝降级；有标签时继续核对原意图身份、确认、候选/Trial及实际来源快照，运行原Gate。批次反降级只读查询同一原意图，不引入第二个权威。

green实际调用CanonicalPredictiveExecutor，缺标签在首次性能访问/engine之前以NOVELTY_CANONICAL_PROTOCOL_METADATA_REQUIRED拒绝，三个原预算桶used0/reserved0，预留按原协议释放。旧v1兼容用原正式启动服务新建真正无新版标记的意图，不是从新版删字段伪装旧记录；旧confirm/recover和原边界正常，预算不变。已有R2完成回执重放/中断同Trial恢复及批次删除metadata回归通过。

`.gitattributes`仅将CA-01的原字节JSON夹具标为-text，防止Windows checkout换行转换破坏审计来源哈希；不改变任何生产文件编码策略。

## CA-03 Paper 提交进度见证

新会话header明确使用paper-engineering-replay-v2，每次不可变事件写入后，以原_atomic_write提交head.json（paper-committed-head-v2），绑定header哈希、累计事件数及最后record_hash。读取、冷恢复和advance均先核对完整事件链与提交头。缺头、缺尾、多条尾部缺失、旧提交头或损坏均阻断，不自动补写或回退。沿用ObjectiveMutationLock的资源锁保护同一会话advance；陈旧内存实例不能覆盖新进度。

旧v1归档仍可只读检查原链，明确LEGACY_UNVERIFIED_HISTORY/committed_history_verified=false；禁止原地续跑、补head或静默升级。兼容夹具取自原ffbff08实际UI合成会话的原字节header与9条事件，附SHA256来源。旧格式本来没有尾部见证，不能宣称能够证明其完整性。

真实engine red：提交9条后移走第9条，原新进程read返回8且advance推进至10。修正后保留丢失证据并拒绝。硬退出测试覆盖第9条事件写完/head未提交（阻断）和head提交后（恢复9并继续10）；原完整engine现金、费用、订单、成交及事件哈希对账仍通过。复用原原子替换，不扩大为整机掉电保证；不抵御同时重写全部历史与见证的无限文件权限。

## CA-04 当前计划必须匹配完整内容

比较接口仍验证两份计划各自的完整内容hash。原七项上下文相同时，再核对plan_id；内容不同返回STALE，changed包含plan_content，不能返回CURRENT_RESEARCH_PREVIEW。已有上下文变化保持原changed解释。完全相同的实际重新生成仍CURRENT，归档只读比较、不覆盖。

source_identity扩展为显式daily-plan-source-v2，按确定性文件名排序绑定corrected、原helper、计划模块、来源定位/common、语义编译器/候选/验证及既有engine组件（退出、现金/仓位、费用、滑点等）。仅身份覆盖变化，不更改这些规则。daily-plan-preview-v1的完整内容合同和存档接口保持兼容；旧source_identity可读，与新版生成明确STALE，不重算旧plan_id或回写历史。

四个red是正式预览生成后，对比较接口输入构造合法的新内容身份，分别变化entries、holdings、readiness_reasons、signal_diagnostics；不声称实际策略产生了不同绩效。源码身份测试只复制源码并追加合成注释，实际计划条目不变但来源身份变更，证明失效判定覆盖费用依赖；不通过修改策略行为造red。

## 部署与回退

先暂停隔离合成工作台写动作，保留整个旧目录及其哈希，再部署固定提交；不操作真实目录。新资格必须走v2请求/真实服务确认，新Paper会话写入独立新输出目录。旧资格与旧Paper保持只读历史，不自动迁移；同目录缺记录或见证不做自动修复。没有改变原CLI/API默认只读或启动恢复行为。

回退源码可逐模块git revert，但必须同时停用对应新版写入口，保留新版目录，不能让旧实现把新版批准/事件当作可继续运行的旧记录。故障目录只用于诊断，恢复须核对完整原件，禁止用重算hash补造成功。

四项模块已完成服务级验证；固定最终HEAD的整体/双平台结果、原始日志/JUnit、base完整差异及ffbff08增量由新版本外部审计包绑定。本文不预先声称CI通过或外审关闭。全路线工程、合成验证与真实运行授权继续分别报告；旧Windows事件及真实未核验不因此关闭。
