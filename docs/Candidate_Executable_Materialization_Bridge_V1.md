# Candidate Executable Materialization Bridge V1

## 业务边界

本阶段只负责把已经完成 Candidate Governance Freeze 的 Candidate Proposal，经过一次只读 Preview 和第二次人工确认，物化为既有的 `DurableFrozenCandidateContractV1`。它不是新的 Candidate 生成器，也不是 Structural、Trial 或 Performance 入口。

正式链路为：

```text
CANDIDATE_GOVERNANCE_FROZEN
  -> CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION
  -> EXECUTABLE_MATERIALIZATION_PREVIEW_READY
  -> EXECUTABLE_CANDIDATE_FROZEN
  -> READY_FOR_STRUCTURAL_PREFLIGHT
```

确认完成后只返回 Structural Preflight 的资格和下一动作，不自动调用 Structural，不预留 Trial 预算，不启动 Trial/Backtest，也不调用 AI。

## Canonical authority

| 工件 | Canonical role | 本阶段用途 |
| --- | --- | --- |
| `CANDIDATE_PROPOSAL.json` | `CANDIDATE_PROPOSAL_FACT` | Candidate Proposal 事实和来源 hash |
| Candidate Governance Freeze Receipt | `CANDIDATE_GOVERNANCE_AUTHORITY` | 治理冻结的 Candidate 身份、hash 和人工 review 证据 |
| `CANDIDATE_REGISTRY.json` | `CANDIDATE_INVENTORY_EVIDENCE` | 冻结 Candidate 的登记证据，不承载执行权威 |
| `durable_frozen_candidate_contracts.json` | `EXECUTABLE_CANDIDATE_AUTHORITY` | 可被后续 Structural 读取的唯一执行合同 |
| Preview、Confirmation Receipt、State Projection | `RUNTIME_PROJECTION` | 可审计的流程投影，不替代上述 canonical authority |

`DurableFrozenCandidateContractV1` 是本阶段唯一可写入的执行合同存储。不得创建第二套执行候选库，也不得把原始 `Mapping` 直接传给 Provider。

## Preview 与确认

Preview 文件为 Candidate Proposal 目录下的 `EXECUTABLE_MATERIALIZATION_PREVIEW.json`，必须包含 Preview 身份、Proposal/Freeze/AI Approval/Contract hash、PIT/数据与执行时序、Provider readiness、缺失字段、警告、`human_confirmation_required=true` 和 `source_bindings`。Preview 创建是只读计算加不可变文件创建，不写 Durable Contract Store。

缺少 `DurableFrozenCandidateContractV1` 所需的字段时，返回 `EXECUTABLE_MATERIALIZATION_INCOMPLETE`，详情必须包含 `missing_required_fields`，并保持 `safe_to_advance=false`。系统不得使用默认值、旧 Candidate、Trial 或 Performance 结果补齐字段。

Preview 会比较治理来源和 Durable Contract 的机制、因子角色/方向、事件时序、入场/确认/退出、排序/选取、持有期、最大持仓、PIT 和执行时序等语义。发现漂移返回 `CANDIDATE_SEMANTIC_DRIFT`，不得进入确认。

确认请求必须显式提供 `confirmed=true`、当前 `preview_hash`、`reviewer` 和 `idempotency_key`。确认回执为 Candidate Proposal 目录下的 `EXECUTABLE_MATERIALIZATION_CONFIRMATION.json`，不可覆盖，记录 Preview、Candidate、Contract、reviewer、时间、幂等键和回执 hash。相同身份与内容重复确认返回幂等成功；同一身份不同 hash 或不同确认上下文返回冲突。

## 恢复与对账

写入 Durable Store 后、写入确认回执前发生中断时，系统可通过现有 Durable Contract 加确认回执的恢复检查识别 `contract_written_receipt_missing`，不得重复追加合同。Proposal、Registry、Preview、Durable Store 的身份或 hash 不一致时 fail-closed，保持 `safe_to_advance=false`。

Objective Reconciliation 的行为如下：

- 只有 Governance Freeze：允许推进到“创建 Preview”，不具备 Structural 资格；
- Preview 就绪：只允许“人工确认执行合同”；
- Durable Contract 有效且已确认：进入 `READY_FOR_STRUCTURAL_PREFLIGHT`，下一动作是 `RUN_STRUCTURAL_PREFLIGHT`；
- Provider 校验失败、字段缺失、语义漂移或 canonical 冲突：阻断，不触发下游动作。

所有读取、Preview 和确认结果都保持 Outcome-Blind；本阶段不加载收益、回撤、胜率、Trial 或 Performance 数据。

## 接口与 CLI

Console 提供只读状态、创建 Preview、提交确认三类动作；Web loopback API 只调用上述 Bridge，不直接调用 Structural 或 Trial。CLI 默认只创建 Preview；确认模式仍要求已存在的 Preview、hash、reviewer 和幂等键，不能绕过人工确认。

完成本阶段的含义是“执行合同已确认，并具备 Structural Preflight 资格”，不是 Structural 已执行，也不是 Trial 已启动。
