# 持续全池研究验收

验收入口是 `scripts/run_continuous_universe_acceptance_v1.py`。默认只读恢复受信部署中已批准的有限总任务，读取原始回执、账户报告和累计预算事件流；不访问模型 gateway，不读取行情表，不补批准、不修改预算，也不派发 worker。报告用于用户验收，不传回策略设计上下文。

```powershell
python scripts/run_continuous_universe_acceptance_v1.py --workspace-root E:/workspaces/research --deployment-config E:/workspaces/research/DEPLOYMENT.json --research-id registered_research
```

部署配置沿用 `scripts.lifecycle_deployment_v2.load_continuous_research(workspace_root, config, research_id)`：顶层 `continuous` 中登记 `submission`、`owner_approval`、`researches` 和可选的 `model_gateway`。`researches[research_id]` 必须对应原 campaign、完整冻结合同、模板、阶段额度和模型上限；Owner 批准记录由固定部署校验。独立资料真实准备完成后，可在该登记项添加 `confirmation.admission={path,sha256,approval_ref}`，引用维护者批准的 `PINNED_INDEPENDENT_ADMISSION_V1` 原件，其可信读取 scope 与 prior-access review 必须绑定同一已冻结协议。无需提前编造未知文件哈希。验收脚本没有创建批准、创建 campaign、增加额度或更换资料路线的入口。凭据只通过部署约定的环境变量提供，不写进报告。

添加 `--json` 输出 canonical English keys 的 JSON；默认输出中文。`PASSED` 且 `delivery_gate_passed=true` 才返回退出码 `0`。缺失真实外部条件时输出 `BLOCKED` 或 `PARTIAL`，并返回 `2`；不能把“检查能运行”当作真实交付通过。

## 分层证据

| 层次 | 核验内容 | 不能由此推出的结论 |
| --- | --- | --- |
| 软件与合成工程检查 | 冻结合同、恢复、只读边界、失败家族历史、调用接线和异常路径的合成单元测试 | 真实模型已调用、真实行情足够、策略有效 |
| 真实模型 | 至少两个不同批次的原始 HTTP gateway 请求、策略、成功回执、模型身份、上下文与输出哈希；硬 token/费用上限和原预算结算匹配 | 生成的规则盈利或取得独立性 |
| 真实全池账户 | 至少两个不同冻结机制组合和不同规则，使用公共 V4 入口、多个已支持指标、BASE/STRESS 独立 5 万元账户；原件核账通过；完整登记分母及逐股排除保持 | 完整历史市场已覆盖，或业务最终标准已达到 |
| 最终探索证据 | 同一候选至少 504 个实际账户日，冻结业务门槛全部满足；预热不计入，空仓日计入 | 252 日独立验证已完成 |
| 独立业务验证 | 完整家族与选择政策先冻结；真实冻结后未见资料及 prior-access 复核；至少 252 个实际账户日，公共核账与报告原件一致 | 自动获得正式统计资格或 Paper 资格 |
| 策略业务目标 | 同一冻结候选的探索与独立阶段均达到冻结标准及报告要求 | 正式方法适用或 Paper 实盘观察完成 |

真实模型核验绑定第二批 `CONTEXT.history` 的全部早先 `RECORD.json` 哈希链、所有规则身份和失败反馈计数；允许冻结容量内的确定性最近历史截断，不允许抹去失败家族。显式合成回执和合法失败回执保留，但不计作真实成功回执。账户机制来自冻结 `allocated_mechanisms` 与 `RECORD.mechanisms`，并要求保留指标身份/角色/表达式拓扑、忽略数值调参后的结构签名不同。描述文字、分配标签或周期参数不同均不足以单独证明机制不同；结构区别只是最低差异证据，不是科学因果证明。

账户核验读取已经生成的报告和结算引用，不重新运行行情或收益计算。完整分母指维护者登记的全范围目标，合格与排除必须无重叠地覆盖全部目标；报告还应披露历史清单完整性限制。登记 TDX/Baostock 来源、manifest 身份、universe 身份和受保护的阶段绑定必须与原任务一致，普通 JSON 中的 `real=true` 不构成证据。

短窗初筛通过后，同一冻结规则可进入预先登记的长周期探索模板。晋级原件位于 `diagnosis_v4/final_exploration/<candidate_id>/EVIDENCE.json`，原初筛 `RECORD.json` 保持不变；该步骤不生成新规则、不调用模型，也不取得独立资格。验收通过 `FinalExplorationQueueV1.evidence()` 重核候选、原记录、模板、公共任务和报告身份，并单独列出 `promoted_final_exploration`。已有原探索本身满足504日及全部最终标准时，也可直接使用其原件。长期账户完成但业务标准未达标的 `FINAL_EXPLORATION_FAILED` 仍保留真实执行事实，不能进入独立确认。

独立协议在冻结时保存 `final_exploration_evidence_refs`，绑定所选候选的原探索或晋级证据及BASE/STRESS报告引用；恢复、准入和业务完成判定均只读重核这些原件与冻结标准。`final_exploration_ready` 名单、短窗 `SCREEN.json` 或自报达标标记不能代替504日原始账户证据。重新运行审计不会替换初筛记录、补发 worker 或刷新授权。

未来TRAIN路线的已登记投影委托还必须携带 `VERIFIED_TRAIN_PROJECTION_ADMISSION_V1`，审计重核其Owner批准原件及范围，仅打开元信息。独立路线无论事先登记dataset还是按可信路线后登记，都必须绑定当前冻结 `PROTOCOL.json`、`ADMISSION.json` 及所选规则；已知dataset身份不会省略独立准入和家族选择检查。

累计预算沿用原 `authorization.json`、`run_budget.json` 和 `run_budget_events.jsonl`。模型真实用量必须出现在同一事件流结算中；UNKNOWN 或仍活动的 worker 段保留为恢复缺口。审计前后会比较预算原件哈希，遇到并发写入也会阻止本轮只读门槛通过，之后可在写入停止后重新审计。报告提供现有余额和未决操作摘要，不创建新余额。

## 最终业务口径

最终合同继续使用原 `USER_STRATEGY_BUSINESS_ACCEPTANCE_V1`，短窗口探索初筛不能降低它。探索不少于 504 个实际账户日；独立业务验证不少于 252 日。BASE 年化净收益至少 15%、最大回撤至多 15%、完整净回合胜率至少 55%、净回合利润因子至少 1.3、完整回合至少 100；STRESS 总净收益严格大于零。各阶段及各成本账户不合并利润。

年化使用 `final_equity / initial_cash` 的 252 日复合口径，权益包含未平仓持仓。胜率与利润因子只用完整关闭回合的净利润。正利润总和大于零、亏损总和为零时满足任何有限的利润因子最低值，报告将数值保留为 `null` 并说明边界政策，不输出非标准 JSON 的 Infinity；全零利润不能通过。

`CONTINUOUS_BUSINESS_DIAGNOSTICS_V1` 保存实际账户日期、固定前后两半的连续净值收益/回撤、证券/完整回合/时期集中度和资金利用率。现金与系统支持的价格对照来自公共最终报告，并绑定其 source result 与结算哈希。价格篮子允许明确披露为不可投资的理论对照；不会新增可投资基准要求。缺报告、缺披露、原件身份冲突或核账未通过都不能宣布业务完成。

确认结果、确认数值和独立资料不进入 `handover(design=True)` 或后续模型上下文。普通状态只公开安全的业务完成标志；用户结果入口可核对原件。正式方法当前不适用不会阻断合法的描述性业务验证；旧 `ValidationProtocolV2` 的 504 日及方法适用要求保持不变。正式资格和 Paper 实际日数始终单列显示。

## 显式推进及当前缺口

仅在已有真实部署、批准和资料准备齐全时，可以显式推进原服务：

```powershell
python scripts/run_continuous_universe_acceptance_v1.py --workspace-root E:/workspaces/research --deployment-config E:/workspaces/research/DEPLOYMENT.json --research-id registered_research --run --max-steps 1 --json
```

`--run` 直接调用已经恢复的 `DiagnosisResearchV4.advance()`，最多指定次数，遇到等待、暂停、撤销或待核对状态停止。它可能消费原已批准模型/worker 额度；不会调用 `start()`、补授权、自动重试付费请求或重建任务。原任务未显式启动时继续保持 `CREATED`。预算未知用量需原授权渠道核对，不能通过新任务绕过。

本次代码实现和合成测试没有取得真实 gateway 的两批付费回执，也没有交付新的 504 日全池账户或 252 日独立观察报告。固定部署已提供 `PinnedIndependentAdmissionV1` 组合入口；没有配置准入时保留 `AUTHENTICATED_INDEPENDENT_DATA_NOT_AVAILABLE`，已固定引用但真实文件尚未部署时保留 `PINNED_INDEPENDENT_ARTIFACT_NOT_DEPLOYED`。快照投影、文件哈希或声明 JSON 都不能替代可信资料登记、Owner 批准的 prior-access 复核及独立读取能力。已有资料若只够短期初筛，应继续显示实际可用日数及最终 504 日差额。

真实验收需要外部维护者提供可核验的受限 gateway 部署、原有限总批准、足量登记全池 TRAIN 资料、真实独立准入与观察。具体缺项以对目标部署执行本脚本的 `blockers`、`evidence_errors` 和 `layers` 为准；不能用 spy 测试报告替代这些原件。本脚本尚未针对用户的真实部署运行，因此本文件不宣称真实交付 gate 已通过。

## 工程验证与回滚

由主代理统一执行相关测试：

```powershell
python -m pytest tests/research_factory/test_continuous_research_contract_v1.py tests/research_factory/test_business_validation_protocol_v1.py tests/research_factory/test_continuous_universe_acceptance_v1.py tests/research_factory/test_campaign_confirmation_v1.py tests/research_factory/test_forward_snapshot_v1.py tests/research_factory/test_validation_protocol_v2.py -q
```

合成测试只验证合同与调用行为，不能成为真实交付证据。回滚验收入口时移除新增脚本、对应测试和本文档即可，不删除 campaign、预算事件流、delegation、模型或账户原件；确认协议回滚也不得复用已暴露的独立资料或重新建立余额。
