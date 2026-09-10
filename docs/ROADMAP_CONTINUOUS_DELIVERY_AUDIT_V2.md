# 连续工程集中审计导航 V2

本版接续用户指定恢复点 e288746490912ae979dd3643f36eaee063335720，纳入分别批准的 A Objective execution_binding 与 B 隔离 synthetic 有界批次，以及原新颖性/使用资格合同。旧 V1 文档与旧集中审计 ZIP 是历史证据，不代表本版仍在等待 A/B 批准；旧 ZIP 不重写。

当前交付准备仍为 PARTIAL，最终结果由新固定 HEAD 的整体回归、双平台与原始证据决定。独立集中审计 PENDING，内部检查不代替独立外审。主需求矩阵的“新版本全路线需求→代码→测试→证据→限制”是当前索引，早期段落按其时间保留。

## 版本与证据边界

- 仓库 a1351995160/trade；主线基线 e72fa6ae0ace0dbff6eeac87ae0e09082431d89a。
- caller完成 4f780cf6454c36124f8a9477ca73098551d49f04，原PR8未合并；总差异包含caller。
- 总分支 codex/roadmap-engineering-completion-v1，唯一Draft PR9；不merge main、不auto-merge、不重写历史。
- 新版原始证据目录 `E:/llmwiki/roadmap-engineering-evidence/final-bounded-execution`。固定HEAD、base/test-merge、事件/attempt、各job/Sonar实际状态、平台patch与依赖、测试清单、完整差异、提交图、逐文件哈希和交付状态写入该目录。尚未生成的结果不能记PASS。
- 旧e288746 ZIP SHA256为0792c467cbc9e9e84871f5bc043d717e29d4183380eea0f9370aee48b407cca3。新包必须另命名、单列manifest与SHA256。

## 实际调用及审阅顺序

```text
新V2 Objective preview/confirm（实际人工测试确认）
 → 原Objective/SearchBudget/MultipleTestingFamily/lineage创建事务
 → 原设计审批、治理冻结、物化确认
 → 完整来源新颖性快照及独立人工确认
 → 实际Structural服务
 → 单候选显式预测启动，或B明确清单的批次preview/confirm
 → BATCH_DELEGATED许可 → 原领域检查/预算/Trial
 → 最后父批准与新颖性复核 → PerformanceAccess → 同锁取得输入快照
 → 锁外原双engine → 原结算/统计/最终裁决/registry/盲化失败回流

同一冻结输入与实际registry
 → 独立计划/Paper测试用途请求/确认
 → 原daily_plan → portfolio_plan（共享现金、归属和冲突）→ 不可覆盖归档
 → 原PaperReplaySession/engine/broker/ledger → 事件、重启与对账
Vue统一工作台 → 正式API → 本机/模式/确认/领域边界
```

四种绑定不能互相提供权限。A只确定目标身份，新颖性只规定比较范围，B只允许明确清单内合成动作，使用资格只允许明确的计划/Paper测试用途。旧v1身份不改，旧CP预测禁令保留。设计侧仍只接收盲化失败信息，不以精确绩效调参。

审阅从以下分组进入，再检查跨模块调用：

| 分组 | 重点代码 | 必查边界 |
|---|---|---|
| 源码/数据/PIT | source_dependencies、caller_inputs、data_readiness、原corrected runner | 文件身份、available_at、双源状态、禁窗前过滤、BASE/小资金分离、F01–F04 |
| 创建/新颖性 | objective_execution_binding、research_proposal_governance、synthetic_novelty/start | preview与身份非循环；原事务；完整来源、不读结果、精确自身排除；旧版本不迁移 |
| 批次/权限/资源 | synthetic_batch、synthetic_batch_delegation、synthetic_batch_resources、synthetic_batch_worker | 父批准新鲜度、明确白名单/额度、真实JobObject或rlimit/时限、无业务模型费用、暂停撤销竞争 |
| 执行/结算/统计 | predictive_trial_start/executor、原预算/TrialLedger/裁决/registry/failure | 性能前/后失败分开、原账本为权威、恢复只结算/不免费重跑、原家族阈值不变 |
| 计划/Paper/组合 | daily_plan、paper_replay、portfolio_plan、strategy_admission、synthetic_usage | 同策略语义、T+1/lot/费用、资格用途与撤销、资金不重占、独立账户不冒充组合资产 |
| UI/运维 | webapp、SyntheticBatchConsole、EngineeringWorkbench、engineering_workspace/backup | 默认只读、GET不写/不恢复、状态解释、本机确认、备份新根失效、源码部署与回滚 |

## 部署、操作与恢复

选择源码checkout部署；不宣称wheel包含脚本和前端资源。使用原 `.github/workflows/requirements-p3b.txt` 的哈希锁安装依赖并执行pip check；前端按原package-lock运行npm ci及build，禁止借用其他环境包或放宽锁。所有合成服务在Python import前设置tests/isolation、CHANLUN_TEST_ISOLATION=1和受保护原根CHANLUN_PROTECTED_ROOT。

已有工作台：源码模块 `python -m chanlun_trader.research_factory.engineering_workspace inspect|serve --config <绝对workbench.json>`，serve只监听127.0.0.1，默认READ_ONLY；需要合成工程操作才加--governed。详细已验命令见主矩阵“正式源码启动入口”。打开页面或重启不会开始研究。

新增完整服务演示及批次API操作见[SYNTHETIC_BATCH_AUTHORIZATION_V1.md](SYNTHETIC_BATCH_AUTHORIZATION_V1.md)。`synthetic_batch_demo.py --port 8789`创建两个全新正式服务合成候选，再在同一工作台核对、确认、执行批次；不自动批准批次或计划/Paper资格。通常页面操作不需手改JSON。该测试驱动器不是生产研究入口；正式API使用应用配置的明确根，HTTP不能替换工作区。

B首版仅固定候选集合、并发1、零重试、NONE模型且模型调用/token/费用均0。不能把配置数值写出即当强制生效：OS资源测试与实际领域资源不足路径需同时通过。来源、合同、政策或批准失效后重新预览确认；不能自动刷新沿用旧确认。

停止新动作使用pause/stop/revoke；已有副作用按原结算。实际控制进程退出后的recover先检查原进程与canonical事实，不启动新worker或重试已开始动作。completed只证明历史。演示用Ctrl+C停止，保留全部合成批准/账本/失败；不安装后台服务或定时任务。

工作台备份恢复使用原engineering_backup明确合成输入/输出范围。复制至新根不能使旧资格或批次批准有效，不迁移身份。回滚按progress中模块git revert；若停用新协议，保留记录并停用该流程，不能用旧代码继续消费新批准。主线不回退。

## 未认证与开放事件

- 真实数据始终NOT_VERIFIED；READY_FOR_REAL_TRIAL=false、R1_FULLY_CLOSED=false。真实Trial/Paper/观察/订单没有授权，真实策略0、观察天数0。
- DAILY/RAW/V2及支持矩阵以外的频率、事件、公司行动、数据源不宣称全覆盖；外部AI/backend仅合成传输，不等于真实调用验证。旧RealFactoryRuntime完整现场运行不因新版明确清单路径通过而自动获得认证。
- L1 run34320730603、L6 run34428154667继续OPEN_ROOT_CAUSE_UNCONFIRMED；a64c49a R1 PR run34461942454原P3C拒绝cmd.exe、exit79原件保留；旧e288本地进程中断原件保留。
- 320e980 PR R1 run34490995583：Ubuntu通过、Windows新增批次夹具来源路径失败；push34490990216取消。d104910修正夹具创建前路径一致性；新平台结果独立报告，不隐藏原失败或对历史事件推定根因。
- 历史test_predictive_trial_start_v1的22项fixture在缺失AI_HANDOFF_V2_e1ddf冻结合同处失败；不从原真实目录补读，不增加skip。最终collect、实际执行、未执行清单分别报告，不将收集数当通过数。
- 本地batch-temp-alias首条命令保护变量名误写导致sitecustomize未安装，并有测试断言字段错误；原日志不可作为隔离认证。独立final日志修正后实际探针通过，不覆盖原件。

全会话合法合成Trial/engine总数未统一埋点时报告NOT_MEASURED，分段提供实际R2/R3/Paper计数；重复回归不是不同业务Trial。禁止动作探针、被拒绝测试尝试、真实执行及合法合成调用分开。原始日志中的缺失计数不填0。

最终包只包含明确源码/测试/工程文档、差异和允许的合成日志；排除真实数据、真实绩效、凭据、.git和虚拟环境。新manifest和ZIP完成隐私检查后交独立集中审计；若仍有必要工程或认证阻断，如实PARTIAL_WITH_BLOCKERS，不把登记待办当完成。
