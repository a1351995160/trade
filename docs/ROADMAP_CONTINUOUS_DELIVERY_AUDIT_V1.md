# 连续工程集中审计导航 V1

当前结论：PARTIAL_WITH_BLOCKERS。本文件覆盖 main 基线 e72fa6ae0ace0dbff6eeac87ae0e09082431d89a 至总集成分支最终固定 HEAD，包含尚未合并的 caller 4f780cf6454c36124f8a9477ca73098551d49f04。最终 SHA、PR、checkout/test-merge SHA、原始日志与哈希由仓库外 final/manifest.json 和 final/delivery.json 绑定，避免为回填 CI 结果反复改变代码 HEAD。独立集中审计 PENDING；内部顺序自检不是独立通过。

本页是当前状态；ROADMAP_IMPLEMENTATION_MATRIX.md 的早期 PARTIAL 段落是历史快照，其后追加记录说明已修复项。工程投影不构成任何运行授权。

## 当前需求→代码→测试→限制

| 路线 | 已实施及实际调用点 | 测试与阶段原始证据（均在外部证据根） | 当前限制与状态 |
|---|---|---|---|
| R1 | caller_inputs → canonical prepare/invoke/result；real_runtime 批次复用；源码资源、文件身份/PIT/available_at 检查 | test_r1_source_closure/data_readiness/snapshot_integration/caller_io_parity/r1_batch_caller_inputs；r1-stage、r1-final-affected、d1-shared-regression | 选定源码checkout、V2/DAILY/RAW/单缓存因子/正常双源PIT已实现并验证；完整批次run未验证；真实数据NOT_VERIFIED，R1_FULLY_CLOSED=false |
| R2 | structural_reconciliation复用既有零动作盲化合同；execution_evidence实际微结构/预算预登记；批次最终裁决使用实际事前新颖性结果 | test_r2_structural_result_boundary/execution_evidence/budget_registration_evidence；r2-fifth、r2-objective-binding-gap、execution-evidence-final、budget-registration-binding、batch-novelty-evidence | BLOCKED/PARTIAL：真实Objective创建缺执行绑定；canonical事前新颖性证据未接入且原gate仍常量True；完整start→结算→裁决→失败回流及其进程恢复未认证，禁止据此使用完整路径 |
| R3 | batch_scope_request只读申请校验及治理UI；原CP有限loop正确报告max_ticks停止 | test_batch_scope_request及原Phase2套件；r3-route-catalog-final、batch-novelty-evidence | 申请/等待/停止已实现；完整有界预测研究编排BLOCKED，批次授权模型WAITING_POLICY_APPROVAL；CP预测禁令保留，不把DENY序列算完整研究 |
| D1 | shared corrected day → daily_plan；workbench实际发布；strategy_admission读取实际registry；SyntheticUsageService请求/人工确认/撤销接入 | test_daily_plan/strategy_admission/synthetic_usage/engineering_workbench；d1-archive、strategy-admission-final、synthetic-usage-expiry/history-green | 合成冻结计划与测试资格范围IMPLEMENTED；只读/显式确认正负向已验。真实可采用计划NOT_AUTHORIZED；T_CLOSE研究窗口，不冒充今天实时计划 |
| D2 | 原engine/broker/ledger逐事件PaperReplaySession；独占事件证据、冷重放对账；实际advance API/UI及用途资格/输入重验 | test_paper_replay/engineering_workspace/synthetic_usage；d2-final-core、workbench-restart-final、synthetic-usage-ui-final/restart-server | 合成独立账户回放IMPLEMENTED；实际退出73/重启与参考路径对账已验。BUY缩量后FILLED是原合同，余量重试NOT_SUPPORTED；真实Paper和市场偏差NOT_VERIFIED，观察0天 |
| M1 | PortfolioPreviewPolicy显式冻结组合配置；共享现金、lot归属和冲突裁剪；统一工作台展示/归档及0/1/多测试资格 | test_portfolio_plan/synthetic_usage；m1-preview-final、synthetic-usage-complete/history-green | 合成组合计划IMPLEMENTED；账户执行按候选独立，不能相加冒充组合资产。真实策略0、真实组合政策WAITING_POLICY；未做收益优化 |
| 运维 | engineering_workspace配置核验/源码inspect/serve；engineering_backup限定树备份/恢复；现有console状态与停止接口 | test_engineering_workspace/backup及原隔离套件；workbench-package-cli/server、workbench-backup-cli/restore-cli | 源码checkout部署与显式合成根；无生产daemon/行情凭据/新定时任务；wheel不在已认证范围 |

上述IMPLEMENTED仅指表内限定合成能力，不表示全路线完成。R2/R3必要缺项必须继续以BLOCKED列出，不能由旁侧组件测试覆盖。

## 实际调用图与审计分组

```text
正式人工Objective创建 ── [执行绑定待批准，未接通] ── 完整设计/冻结/Structural/Trial
原真实Provider → Structural结果持久化（已修复）
原显式预测授权/start → canonical execute（完整R2未认证，事前novelty缺口）
CP/治理页 → 实际安全上下文 → 有限tick/只读范围请求 → 人工等待；预测仍禁用

显式synthetic workbench.json → canonical合同/政策/reader/PIT/输入准备
  → 实际registry只读事实 + 合成测试资格请求→人工确认→撤销
  → daily_plan（同runner信号/退出）→ portfolio_plan（共享现金/冲突）→ 不可覆盖归档
  → 显式advance → 原engine/broker/ledger事件 → 独占事件存储 → 重启重放/逐项对账
Vue /research/workbench → 实际API → 既有本机/模式/确认/context/锁 → 上述领域服务
GET只读；打开页面/启动应用不会自动推进
```

- 数据/PIT：caller_inputs、data_readiness、source_dependencies、scripts/run_engine_corrected_phase4_v3.py；对照R1文件读取和available_at负向。
- 执行/恢复：engine/engine.py、paper_replay、engineering_workspace、原predictive_trial_start与P3B/P3C；区分已验Paper恢复与未验完整R2恢复。
- 权限/预算：synthetic_usage、engineering_workbench、webapp、execution_evidence、trial_adapter、batch_scope_request；原canonical批准/预算仍唯一来源。
- 统计/outcome：structural_reconciliation、real_runtime最终gate、predictive_executor已知缺口；原PerformanceBlindGuard/FinalResearchAdjudicator阈值未改。
- 计划/Paper/组合：daily_plan、portfolio_plan、strategy_admission、paper_replay；qualified synthetic不等于统计通过。
- UI/运维：frontend/src/console/components/EngineeringWorkbench.vue、BatchScopeRequest.vue、engineering_workspace/backup。UI与API均拒绝无确认/陈旧/失效用途。

## 十五项跨系统验收判定

| 附件场景 | 当前证据与判定 |
|---|---|
| 1 干净部署/cwd/双root/只读 | 原哈希锁新venv、R1冷子进程、workspace不同cwd子进程、两个root身份拒绝、真实浏览器默认只读；PASS限定源码部署 |
| 2 文件→真实reader/合同/runner | R1直接参考与BASE/10K独立engine；PASS支持矩阵内 |
| 3 人工治理→完整Trial→裁决→回流 | Objective真实创建及Structural/授权片段存在，但完整组合BLOCKED，NOT_PASS |
| 4 授权/陈旧/损坏拒绝 | 原P3C授权一致性及新工作台/资格/执行证据负向；PASS已执行套件范围，非完整R2 |
| 5 有界循环/等待/撤回/停止 | 原CP门禁、max_ticks、只读范围请求、合成资格撤销；PARTIAL，未启用批次预测权限及其资源强制执行 |
| 6 子进程/恢复/竞争 | 原P3B/C加Paper真实退出73与冷恢复；PARTIAL，完整R2统计/预算组合中断未验证 |
| 7 时间/PIT/价格/身份/禁窗 | 原R1/available_at/lookahead与新计划负向；PASS支持矩阵内 |
| 8 全局outcome blindness | 原设计/CP安全投影及Structural零动作修复已验；PARTIAL，canonical新颖性比较集来源未建立，不认证全局组合 |
| 9 数据不足/零信号/工程错误 | R1 caller和计划NOT_READY/NO_TRADE负向；PARTIAL，完整R2最终REJECTED回流未跑通 |
| 10 回测/计划/Paper同语义 | 实际runner对照信号/退出；Paper完整orders/lots/trades/费用/现金一致；PASS确定性合成时序 |
| 11 0/1/多个资格 | 实际资格服务0/1/两个、空库/版本/退役过滤；PASS仅测试使用资格，真实0 |
| 12 现金/同股/冲突/部分成交/T1/重启 | M1共享现金及退出归属，D2真实SELL部分成交/T1/重启对账；PASS原合同范围，BUY余量重试不支持 |
| 13 证据/身份/不可覆盖/写入根 | 归档损坏/复制改根/输入改变/资格历史丢失、备份内容hash；PASS对应组件 |
| 14 CLI/Web/领域 | 源码CLI装配实际同服务；API与领域门禁、真实浏览器操作；PASS工程入口，R2未接通单列 |
| 15 原安全/Windows事件 | 最终固定HEAD原阶段套件与新测试认证结果见外部manifest；L1/L6仍OPEN_ROOT_CAUSE_UNCONFIRMED |

不存在已经跑通的全R1→R2→R3→D1→D2→M1贯通链。已跑通的是明确合成文件→实际caller→测试资格→计划/组合→实际Paper→重启对账→撤销拒绝这一子链。

## 必要缺项与集中批准待办

1. R2 Objective execution_binding新版本协议：具体范围/兼容/回滚/测试已在ROADMAP_IMPLEMENTATION_MATRIX列明，WAITING_POLICY_APPROVAL。本次收到的仅合成测试资格批准不涵盖它。
2. R2 canonical事前新颖性：当前candidate_similarity_control仍常量True；不能作为已验证硬门禁。需要权威、冻结、无绩效的比较集来源并接入事前校验；不默认为空历史，不从真实报告补读。完整执行入口不应据本次验收启用。批次已使用实际新颖性结果不代表canonical已修复。
3. 以上完成后仍须真正跑通R2结算/统计/registry/失败回流及真实进程恢复，当前NOT_VERIFIED。22项旧test_predictive_trial_start夹具缺未交付AI_HANDOFF_V2_e1ddf合同；不读取原研究根或伪造权限补齐。
4. R3新批次授权模型及全资源强制执行：本次只有合法申请/校验，不自签批次运行权；完整有界预测研究仍缺项。现有CP预测禁令保持。
5. 真实A数据核验、B单Trial、C批次、D Paper窗口/启动、E真实策略/组合准入按主矩阵集中列明；候选/合同/日期/字段/只读根/输出根/预算/停止条件未得到具体授权前不可执行。真实观察0天，不能压缩替代。

## 最少部署与恢复

在隔离源码checkout使用已认证Python（Windows3.13/Linux3.11）、原requirements-p3b.txt哈希锁，pip check；frontend按原package-lock npm ci --ignore-scripts、npm run build。保留tests/isolation作为import前保护，设置显式CHANLUN_PROTECTED_ROOT，不能为了启动关闭隔离。详细命令见主矩阵的“正式源码启动入口”。

首次演示：使用一个不存在的绝对临时root，PYTHONPATH加入tests/research_factory后运行 tests/research_factory/workbench_demo.py --root <新绝对root> --port 8857 --governed；它生成纯合成文件并写workbench.json。停止后通常启动使用源码模块 engineering_workspace serve --config <绝对workbench.json> --port 8857；默认只读，工程操作才显式加--governed。页面生成请求→再次勾选确认→计划归档/累计事件推进；不需要手改资格JSON。

备份/恢复只用engineering_backup既有CLI和明确源/新目标，验证SHA256后从新根重新确认测试资格；跨根旧资格失效。恢复不自动推进。配置/输入改变时拒绝续跑，保留原账本并重新审查来源，不能改hash“修复”。事件写入失败时停止该会话，重新构造逐项对账；拒绝时保留原始诊断，不提高timeout或放宽隔离。

回滚先Ctrl+C停止合成服务；保留外部证据和合成历史；使用各progress条目的git revert或从caller 4f780cf单独建立回滚checkout，不回退main、不覆盖旧新格式。新资格使用过的根不能回到缺资格门禁的旧实现继续执行。

## 最终证据与未执行项

最终本地按原R1工作流原选择器执行Phase1/2/P3A/B/C和R1新套件，另覆盖batch_scope_request与结构持久边界；前端test/build、compile/collect/diff均独立留日志。collect数不是passed。原Phase1选择器排除现场依赖不扩大，旧legacy module skip不扩展。最终原始结果与平台patch/文件系统、PR/Sonar实际状态由外部delivery.json报告；PENDING不是SUCCESS。

全部pytest不宣称通过：旧现场测试依赖未获准实际合同/报告；已观测22项预测start夹具失败及2项旧console/结构现场依赖失败保留原始日志，未把环境失败当业务red。历史CI只读路由遗漏及本轮真实资格历史回退缺陷均有red/green，不隐藏。

计数按可观测边界分别报告：R1_CALLER_ENGINE_COUNTS实际engine.run；P3A合法模板/治理调用与禁止执行器探针；Paper实际事件/成交/usage-actions文件。工作台手动停止未导出退出探针，不能虚报为0。完整R2合成Trial尚未发生。各次重复回归不能相加成独立业务Trial；未经统一完整埋点的全会话调用总数保持NOT_MEASURED，附原始分段计数。

审计包仅含明确源码/测试/工程文档差异与允许的合成诊断日志，不含真实研究数据/绩效、凭据、.git或venv。最终外部清单逐文件SHA256和ZIP SHA256供复核。MAIN_MERGED=false、AUTO_MERGE=false、REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED、READY_FOR_REAL_TRIAL=false、EXTERNAL_CONSOLIDATED_AUDIT=PENDING。
