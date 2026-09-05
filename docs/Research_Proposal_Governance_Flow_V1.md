# Research Proposal Governance Flow V1

## 1. 目的

本流程把 Research Proposal 与新 Research Objective 创建隔离开。Proposal 可以被人工审核，但任何批准动作都不会自动创建 Candidate、启动 Trial、调用 AI 或消费预算。

流程固定为：

```text
Research Proposal
    ↓
Human Review
    ├─ reject → REJECTED → CLOSED
    └─ approve → APPROVED → OBJECTIVE_CREATION_READY
                              ↓
                       Objective Creation Preview
                              ↓
                    Human Confirm: CREATE_OBJECTIVE
                              ↓
                       New Research Objective
```

系统在 `OBJECTIVE_CREATION_READY` 阶段停止，等待第二次人工确认；不会运行自动研究循环。

## 2. 状态与治理含义

| 状态 | 含义 | 系统行为 |
| --- | --- | --- |
| `CREATED` | Proposal 已持久化 | 等待人工审核 |
| `HUMAN_REVIEW_REQUIRED` | Proposal 等待人工决定 | 只读，可执行 `approve` 或 `reject` |
| `APPROVED` | 审核记录已批准 | 只作为审核过渡证据，不代表已创建 Objective |
| `OBJECTIVE_CREATION_READY` | 创建方案已生成 | 等待第二次人工确认，不消费预算 |
| `REJECTED` | 人工拒绝 Proposal | 不生成创建方案；可关闭 |
| `CLOSED` | 拒绝流程已关闭 | 不再推进研究目标创建 |

批准后的当前治理状态是 `OBJECTIVE_CREATION_READY`，同时治理历史保留 `APPROVED` 中间状态。这样既能表达批准事实，也不会把批准误认为 Objective 已经创建。

## 3. 接口

以下接口由本地 Web Console 使用。写接口只接受本机请求。

### 3.1 Proposal 列表

```http
GET /api/research/evolution/proposals?objective_id=<objective_id>&status=ALL
```

`status` 支持：

- `ALL`：全部 Proposal；
- `PENDING`：`CREATED`、`HUMAN_REVIEW_REQUIRED`；
- `APPROVED`：`APPROVED`、`OBJECTIVE_CREATION_READY`；
- `REJECTED`：`REJECTED`、`CLOSED`。

### 3.2 读取单个 Proposal

```http
GET /api/research/evolution/proposals/<proposal_id>
GET /research/evolution/proposals/<proposal_id>
```

读取不会改变 Proposal、治理历史、预算或任何 Objective 文件。

### 3.3 人工审核

```http
POST /api/research/evolution/proposals/<proposal_id>/review
Content-Type: application/json

{
  "action": "approve",
  "reviewer": "human-reviewer",
  "proposal_hash": "<当前 Proposal hash>",
  "research_direction": "event_driven",
  "reason": "审核意见"
}
```

`action` 只能是 `approve` 或 `reject`。每条审核记录必须包含：

- `review_id`：同一 Proposal 与审核动作的幂等身份；
- `reviewer`：人工审核人；
- `timestamp`：UTC 时间；
- `proposal_hash`：审核时绑定的 Proposal 身份哈希。

`approve` 只写入审核记录、生成 `ObjectiveCreationPreview` 并将 Proposal 推进到 `OBJECTIVE_CREATION_READY`；`reject` 只追加拒绝治理记录并进入 `REJECTED`。

拒绝后的治理收口接口为：

```http
POST /api/research/evolution/proposals/<proposal_id>/close
Content-Type: application/json

{"reviewer": "human-reviewer", "reason": "关闭说明"}
```

该接口只允许 `REJECTED → CLOSED`，不会创建任何研究产物。

### 3.4 读取创建方案

```http
GET /api/research/evolution/proposals/<proposal_id>/preview
GET /research/evolution/proposals/<proposal_id>/preview
```

Preview 状态固定为 `READY_FOR_CONFIRMATION`，至少展示：

- 目标名称与研究方向；
- 父 Proposal 及 Proposal hash；
- parent lineage；
- mechanism coverage；
- 预算建议；
- multiple testing family 建议；
- `budget_consumed: false`；
- `requires_human_confirmation: true`。

Preview 是非执行计划，不创建预算登记、不预留预算、不创建 Candidate 或 Trial。

### 3.5 第二次人工确认

```http
POST /api/research/evolution/proposals/<proposal_id>/confirm
Content-Type: application/json

{
  "confirmed": true,
  "action": "CREATE_OBJECTIVE",
  "confirmer": "human-reviewer",
  "proposal_hash": "<Preview 中的 Proposal hash>",
  "preview_hash": "<Preview hash>",
  "confirmation_token": "<Preview confirmation token>",
  "idempotency_key": "<本次确认请求身份>"
}
```

只有 `confirmed: true`、动作是 `CREATE_OBJECTIVE` 且 Proposal/Preview 哈希与确认令牌均匹配时，才会执行创建事务。

## 4. 最终创建产物

确认事务使用暂存文件、哈希校验、事务 journal 和创建回执，支持进程重启后的恢复。成功后生成：

1. immutable objective id 与 `lifecycle_state: CREATED` 的 Objective；
2. `data/research/research_factory/lineage/<objective_id>.json`；
3. 新 Objective 对应的 `search_budget_registry.json`，初始 `used=0`、`reserved=0`；
4. 预注册的 multiple testing family；
5. `objective_creation_governance.json`；
6. `objective_creation_receipt.json`。

新 Objective 的 `activation_policy` 为 `MANUAL_ONLY`，`activation_authorized` 为 `false`。创建完成后系统停止，不自动激活、不生成 Candidate、不启动 Trial、不调用 AI。

## 5. 安全边界

| 动作 | 创建 Objective | 创建 Candidate | 启动 Trial | 调用 AI | 消费预算 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `GET Proposal` | 否 | 否 | 否 | 否 | 否 |
| `approve` | 否 | 否 | 否 | 否 | 否 |
| `reject` | 否 | 否 | 否 | 否 | 否 |
| 读取 Preview | 否 | 否 | 否 | 否 | 否 |
| `CREATE_OBJECTIVE` 确认 | 是 | 否 | 否 | 否 | 否（只登记零使用预算） |

审核历史与创建回执均采用追加式治理证据。重复审核和重复确认不会生成第二个 Objective；如果确认事务在中途重启，后端只恢复已有的显式确认事务。

## 6. Web Console

页面地址：`/research/evolution/proposals`。

页面提供：

- `待审核`、`已批准`、`已拒绝` 状态筛选；
- Proposal 来源、失败机制、建议方向、机制覆盖和治理历史；
- `审核通过`、`拒绝`；
- 批准后的 Objective Creation Preview；
- `确认创建下一研究目标` 二次确认对话框。

页面不会自动跳转到运行控制，不会自动激活新 Objective，并明确显示 Candidate、Trial、AI 和预算安全边界。

## 7. 验证

治理测试覆盖：

- Proposal 状态流转与审核证据；
- approve 不创建 Objective；
- reject 不改写原研究历史；
- Preview 不消费预算；
- 只有 Confirm 创建 Objective、lineage、budget registry 和 governance record；
- exact-once 重复确认；
- 重启后的确认事务恢复；
- Web Console 读、审、Preview、确认接口。
