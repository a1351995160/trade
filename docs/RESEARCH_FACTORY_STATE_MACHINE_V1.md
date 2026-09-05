# Research Factory State Machine V1

标准状态顺序为：

`CREATED → BUDGET_RESERVED → DESIGNING → HYPOTHESES_FROZEN → CANDIDATES_FROZEN → STAGE1_VALIDATING → PERFORMANCE_VALIDATING → CLASSIFYING → FAILURE_EXTRACTING → COMPLETED`

控制状态为 `BLOCKED`、`ENGINEERING_BLOCKED`、`BUDGET_EXHAUSTED` 和 `INVALIDATED`。每个 transition 必须显式、带原因、带 timestamp，并写入 audit hash。

Resume 从 checkpoint 中的状态继续。如果状态是 `CANDIDATES_FROZEN`，恢复路径直接进入 Stage1；必须复用 checkpoint 中的 candidate set hash，不得重新调用 Hypothesis Generator 或 Candidate Builder。

每个 checkpoint 至少保存 objective、batch plan、hypothesis set、candidate set、budget head、trial ledger head、failure snapshot、validation policy、dataset 和 code identity hash。
