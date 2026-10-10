# 持续全范围研究

`DIAGNOSIS_RESEARCH_V4` 在一份有限总授权下持续提出候选、解释失败、运行正常及压力成本账户，并保留独立验证队列。它使用 `FULL_UNIVERSE_SUBMISSION_V4` 和 `RESEARCH_RULE_STRATEGY_V4`。每次 `advance` 最多派发一个实际工作进程段或一个模型请求；读取状态和接手包不派发。旧有限任务的 `stages/max_calls`、旧 V1～V3 提交和工程凭证保持原语义。

当前提供工程入口，**真实连续 AI 和未来独立资料的完整运行尚未验收**。默认 Codex 没有已核实的单次 token/费用硬上限；缺少实际受信预算网关时显示 `WAITING_MODEL/HARD_BUDGET_UNSUPPORTED`。已冻结账户可以沿原任务与消费记录继续推进。合成测试、HTTP 接线或历史账户工程验收均不能代替真实策略证据。

本次交付范围、真实资料缺口与未完成的验收见[交付说明](CONTINUOUS_UNIVERSE_DELIVERY_20261010.md)。

## 合同、额度与等待

部署前冻结 `CONTINUOUS_RESEARCH_CONTRACT_V1`：全部登记目标、资料合格范围、五万元单账户、仓位、正常与压力成本、指标用途、机制组合、数值排序、同股过滤、区间、资源和最终业务标准。探索初筛只分配后续资源，不降低 504 日探索和 252 日独立验证等最终标准；业务检查与原资格审查分别保留。

总授权和探索、确认阶段储备共同约束候选、资料实验、账户、模型调用、token、微美元费用、核验和活动计算秒。探索不能消耗确认储备。资源或有限总轮次用完进入 `WAITING_TOTAL_AUTHORIZATION`，不代表目标完成，也没有默认五轮完成规则。新增额度必须引用新的 Owner 审批；原消费、未知预留与失败历史不清零。撤销后不能通过恢复取得权限。

## 统一部署

UI、CLI、宿主和验收工具共用 `scripts.lifecycle_deployment_v2.build_lifecycle_service`。顶层兼容原 `bindings`、`real_binding_ids`，持续研究增加如下结构；尖括号是占位，不能直接执行：

```json
{
  "bindings": {},
  "continuous": {
    "submission": {
      "version": "FULL_UNIVERSE_DEPLOYMENT_V1",
      "roots": {"registered": "data/registered"},
      "datasets": [{"dataset_id": "exploration", "root_id": "registered", "manifest_path": "manifest.json"}],
      "authorizations": {"existing_parent": {"path": "authorization/parent.json", "sha256": "<原件SHA256>"}},
      "output_root": "reports/continuous_execution"
    },
    "owner_approval": {
      "store_root": "<受保护绝对审批目录>",
      "token_env": "RESEARCH_OWNER_TOKEN",
      "token_sha256": "<Owner专用令牌SHA256>"
    },
    "researches": {
      "registered_research": {
        "campaign_root": "<工作区内绝对目录>",
        "authorization": "<完整总授权对象>",
        "contract": "<完整冻结合同对象>",
        "stage_limits": "<EXPLORATION和CONFIRMATION完整资源对象>",
        "template": "<V4模板对象>",
        "model_limits": {"timeout_seconds": 60, "max_tokens": 2000, "max_cost_microunits": 1000, "context_max_bytes": 200000},
        "candidates_per_batch": 1
      }
    }
  }
}
```

授权、合同和阶段储备须来自实际登记原件。模板保留父 `authorization_ref`，去掉候选 `rule`、`strategy_id` 和 `research_binding_ref`；固定 `version=FULL_UNIVERSE_SUBMISSION_V4`、`phase=EXPLORATION`、`purpose=EXPLORATORY`。四个模型限制均为正整数，费用为微美元。恢复时逐项核对原合同、模板、限制和审批，不能静默替换旧配置。

短窗初筛可预先配置 `researches.<id>.final_exploration_template`，字段与 `template` 相同；只允许扩大日期、改用已批准的 504 日分段计算规格及对应观察窗口。资料、全池、父授权、资金、仓位和成本保持同一范围，最终窗口覆盖初筛窗口。只有初筛通过的同一规则进入晋级，不再调用模型；公共资料、两成本账户、验证和报告继续占用 EXPLORATION 与总批次额度。晋级的 `FINAL_<candidate_id>` 批次与不可变 `final_exploration/<candidate_id>/EVIDENCE.json` 独立保留，原初筛 RECORD 不改写。未配置或资料不足时等待最终探索资料，初筛探索仍可继续；504 日账户未满足全部最终标准时不能进入独立验证或宣称目标完成。

可选 `researches.<id>.history_references` 保存旧研究原始引用列表，每项为 `{path, sha256, source, metering}`；`source` 为 `SESSION_AI_MANUAL` 或 `LEGACY_SYSTEM`，`metering` 为 `REFERENCED_CANONICAL_LEDGER` 或 `UNKNOWN`。路径与 SHA 在固定部署中冻结，恢复核对原件；未知旧消费不补造调用记录或伪装成已知用量。

未来 TRAIN 投影入场在同一登记下成对配置 `exploration_admission={path,sha256,approval_ref}` 与 `train_projection_deployment={path,sha256}`。前者完整原件采用 `OWNER_TRAIN_PROJECTION_ADMISSION_V1` 并经维护者批准；后者固定登记投影部署。两组引用冻结进 CONFIG，恢复不能换 pin。公共推进的无参固定 loader 核验官方投影回执、同合同与 route、完整子资料分母及原投影授权；只准入登记，账户资料资格仍由受限 provider worker 审计。状态与预览不解引用该入场文件，不读取价格。

公共 preview 返回 `CONTINUOUS_RESEARCH_PREFLIGHT_V1`：只核登记元信息、部署模型是否配置、现有权限储备与磁盘可用性；不连接模型网关。不从 manifest 长度推断实际账户日，没有 canonical 账户证据时账户日与预热日为 UNKNOWN，保持最终业务待证。预检等待用于说明缺口，实际可推进的已冻结用途仍由原公共入口检查。

部署文件、审批目录和 Owner 环境由维护者保护。运行端只持有只读 `OwnerApprovalStoreV1`；HTTP 的 `confirmed=true`、批准文字、任意文件路径和模型输出不能签发 Owner 批准。这依赖受保护部署和文件系统，不宣称能抵御同机管理员。普通 UI、宿主进程不应持有 Owner 专用令牌。

## 预览、批准、启动与接手

文件路径均位于显式工作区内。先预览完整范围，随后在维护者受保护环境中批准：

```powershell
python scripts/run_trusted_research_v1.py continuous-preview --workspace-root <工作区> --deployment <统一部署.json> --research-id <登记ID>
python scripts/run_trusted_research_v1.py continuous-owner-approve --workspace-root <工作区> --deployment <统一部署.json> --research-id <登记ID> --approver <维护者身份>
```

Owner 命令检查令牌与固定 SHA256 后，注入一次进程内对象能力，将具体摘要写入审批目录；不输出令牌。保存返回的 `approval_ref` 到引用文件，运行端随后消费：

```powershell
python scripts/run_trusted_research_v1.py continuous-create --workspace-root <工作区> --deployment <统一部署.json> --research-id <登记ID> --approval-reference <审批引用.json>
python scripts/run_trusted_research_v1.py continuous-status --workspace-root <工作区> --deployment <统一部署.json> --research-id <登记ID>
python scripts/run_trusted_research_v1.py continuous-start --workspace-root <工作区> --deployment <统一部署.json> --research-id <登记ID>
python scripts/run_trusted_research_v1.py continuous-advance --workspace-root <工作区> --deployment <统一部署.json> --research-id <登记ID>
python scripts/run_trusted_research_v1.py continuous-handover --workspace-root <工作区> --deployment <统一部署.json> --research-id <登记ID>
```

`create` 只登记，`start` 标记允许推进，不代替模型预算或资料授权；`advance` 每次只执行一个有界步骤。`continuous-pause/resume/revoke` 需 `--reason`。接手沿用同一研究 ID 和 campaign，不重建余额。

设计接手包包含初筛历史、已核验最终探索的定性失败码与证据身份、累计及阶段消费；它不查询独立验证状态，也不包含独立收益、行情或诊断。查看完整业务状态使用普通 `continuous-status`，不要把该完整结果交回策略设计端。

增量使用 `continuous-preview --grant <增量.json>`、`continuous-owner-approve --grant <增量.json>`、`continuous-grant --grant <增量.json> --approval-reference <新审批引用.json>`。增量绑定原授权身份，逐资源与逐阶段列出 delta、轮次 delta 和有效期，不能改写合同或历史。

宿主沿用现有 daemon 命令并指定 `--lifecycle-bindings <统一部署.json>`，仅推进明确启动的登记研究。每个研究每 tick 最多一个有界步骤。中断后核对原工作进程和回执，活跃或未知用途不能重复派发；停止宿主不清除原预留与账本。

UI 使用 `python scripts/run_ui.py <端口> --research-root <工作区> --lifecycle-config <统一部署.json>`，默认只读。维护者显式加 `--allow-trusted-research` 后，`/research/lifecycle` 可查看总消费、未知预留、阶段储备、候选、等待原因和接手包，并操作已有批准对象。按钮不能签发 Owner 审批。

HTTP 使用固定部署 ID：`GET /api/research-lifecycle/continuous/{id}`、`/preview`、`/handover`；写入口为 `POST /api/research-lifecycle/continuous/action`，内容 `{research_id,action,payload}`。创建和增量需已有 `approval_ref`；暂停、恢复、撤销需 `reason`。请求不能提供部署配置、模型 URL/header、模块或账户工具。

## 模型网关的真实前提

可选 `continuous.model_gateway={endpoint,model_id,trusted_contract_sha256,bearer_token_env}`。固定 HTTPS endpoint 不能含凭证、查询或 fragment；仅访问 `/v1/contract`、`/v1/policies`、`/v1/invocations` 和原请求 ID 查询路径，禁用重定向及代理继承。Bearer 仅从指定环境读取，不写入报告、日志或上下文。各恢复对象独占策略状态。

真实服务合同为 `TRUSTED_MODEL_BUDGET_GATEWAY_V1`，绑定供应商、固定模型、USD、token/费用上限、单次最多一次付费派发、部署/计价证据 SHA256 和有效期。服务必须实际强制：派发前精确输入计数；输出上限包含推理 token；原子保留最坏费用；全部财务责任不超过预留；禁止工具、文件、外部输入及会话；同一请求 ID 只付费派发一次；未知结果持续保留最坏消费；策略安装和查询不收费。

**客户端合同字段不证明远端真的执行硬限制。** 缺部署实现审查、计价/费用责任证明和真实调用回执时，现场验收仍阻塞。timeout 只约束等待，不能当成供应商取消或费用上限；默认 Codex 适配器没有这些保证。

客户端先保存唯一 ID 和内容/schema/策略/合同哈希，再 POST 一次；中断后仅查询原 ID，找不到也不重发。回执绑定全部身份并提供 input/output/total token、费用和调用数。未知继续预留，超耗保留实际债务，无效 JSON 仍记录消费。脚本响应夹具不是现场模型证据。

供应商输入计数与计价应核对官方 [token counting](https://developers.openai.com/api/docs/guides/token-counting) 和 [pricing](https://developers.openai.com/api/docs/pricing)；本项目不硬编码会变动的价格，不把账户设置或客户端 timeout 推定为单次费用责任上限。自定义供应商需自己的证明。

## 独立资料、审核原件与等待

设计端不读取独立资料、收益或诊断。业务协议冻结完整家族和选择规则，独立结果只用于用户报告；等待未来资料不停止仍获授权的探索。正式统计方法与 Paper 资格分别判断。

未来读取需 `submission.trusted_data_deployment={path,sha256}` 指向受保护 `TRUSTED_RESEARCH_DATA_DEPLOYMENT_V1`，其中登记 Owner 审批目录和资料授权原件。父授权的 `trusted_deployment` 必须与该 pin 一致，`trusted_data_access` 固定授权引用、配方和协议/审核身份。请求或 INPUT 的自报路径不是可信部署。

可选 `researches[id].confirmation.admission={path,sha256,approval_ref}` 指向 Owner 批准的 `PINNED_INDEPENDENT_ADMISSION_V1` 完整原件，包含 `snapshot_store_root`、`snapshot_ids`、`calendar`、独立 `request_fields`、`qualification={path,sha256}`、`trusted_data_access`，以及可选 `prior_access_review={path,sha256,approval_ref}`。状态与接手不解引用这些资料；推进时实际核对 scope、协议字节 SHA、完整登记分母与 SnapshotStore 投影。

`RESEARCH_DATA_QUALIFICATION_V1` 仅审核元数据和已知暴露，未匹配暴露仍是 `UNKNOWN`，不能证明从未访问。缺少独立审核原件时保留 `CANONICAL_INDEPENDENCE_AUTHENTICATION_UNAVAILABLE`，不接受自报 `source_authenticated=true` 等字段。

可用独立路径还需维护者**实际完成**先前访问和注册资料来源比对，登记 `CANONICAL_INDEPENDENT_DATA_REVIEW_V1`。该固定原件字段为 `protocol_identity`、`protocol_sha256`、`dataset_id`、`manifest_sha256`、`snapshot_projection_hash`、`snapshot_refs_identity`、`qualification_report_hash`、`exposure_records_hash`、`reviewed_at`、`evidence_refs`、`trusted_route`，以及固定 `review_method=OWNER_PRIOR_ACCESS_REVIEW_AND_REGISTERED_SOURCE_PROVENANCE_V1`、`review_outcome=APPROVED_FUTURE_UNSEEN`。`trusted_route` 必须与合同独立路线完全一致；未来 route 身份只从受保护审核原件读取。资料 scope 的 `independent_evidence_id` 绑定审核内容哈希，`independent_evidence_sha256` 绑定原件字节。

loader 实际核对上述身份、原件 SHA 和审阅时序，再消费受保护 Owner 对完整审核的批准；审核者必须对注册资料与真实快照的来源一致性负责。批准元数据报告本身不会升级来源真实性，合成 SnapshotStore 也不会成为真实来源认证。固定 builder 仅在确认通道通过 `independent_authority` 读取已批准 admission 的资料 scope；普通探索授权不解引用未来资料，也不会因未来资料缺失而停下。

Owner 可用 `continuous-owner-approve-evidence --evidence <原件.json> --approver <维护者身份>` 加同样的工作区、部署、研究 ID 参数登记上述审核、admission 或 `TRUSTED_RESEARCH_DATA_ACCESS_AUTHORIZATION_V1`。此命令不生成行情或替代实际审核。未部署真实资料、审核原件或够长未来窗口时继续 `WAITING_DATA`。当前没有这些现场验收证据，不能把隔离样本写成已完成真实独立验证。

回滚时停用持续宿主，恢复原部署配置及代码版本；保留 campaign、Owner 审批、计算和模型回执。不要删除账本、未知预留或冻结账户来获得新额度。
