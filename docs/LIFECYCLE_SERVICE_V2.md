# 统一生命周期入口

`LifecycleServiceV2` 将现有数据资格、验证协议、策略档案、正式评审、Paper、组合与每日计划汇总到同一个只读视图，并把有限任务绑定到原业务服务。

用户看到的是每条研究或观察任务的真实状态：等待数据、等待资格、已经完成某个阶段、到期、暂停或需要恢复。任务完成不会被显示成“策略有效”；策略资格始终来自原策略档案和评审，真实观察天数始终来自 Paper。

## 已接入的实际服务

| 类型 | 原服务与行为 |
|---|---|
| 数据资格 | `audit_data_qualification` + 原治理暴露记录，只读计算允许用途 |
| 验证协议 | `load_protocol`，核对协议、家族与档案绑定 |
| 策略档案 | `BoundedStrategyArchiveV1.admission`，展示正式观察资格 |
| 研究 | `DiagnosisResearchV1.tick` 或 `BoundedResearchSessionV2.tick`，继承已有会话与预算 |
| 采集 | `SnapshotStoreV1.capture_tdx`，仍由原服务核验真实日期、窗口、来源和字段 |
| Paper | `ForwardPaperSessionV1.ingest`，使用原引擎、账户与提交链 |
| 正式评审 | `FormalAssessmentServiceV1.run`，仍检查家族、窗口、方法、用途与原确认额度 |
| 组合 | 只读展示 `PortfolioQualificationV1.readiness`；显式任务调用原 `review` 核对完整观察账户 |
| 每日计划 | 读取原 Paper 已提交的 `next_plan`，不新增订单或券商执行 |

V2 研究的合格数据 loader 必须由部署代码明确绑定；没有 loader 时显示 `WAITING_DATA`，不回退到旧双证券试验数据。真实采集只能绑定已有 Paper 或正式评审计划，采集证券必须与其范围一致。

## 配置与调用

部署配置 `bindings` 是固定名称到业务对象的映射。所有路径必须位于显式工作区内，不允许链接重定向；HTTP 只能引用已配置的名称和已有任务，不能提供新的数据路径、shell 命令或 Python 模块。

Paper 可绑定已经存在的快照，或通过 `capture_jobs` 引用预先冻结的采集任务及阶段。后一种方式可以在未来快照尚未到达时创建观察任务；读取采集阶段的已提交回执后，再由原快照服务验证并送入原 Paper。不会靠修改绑定中的快照编号改变已经冻结的任务。

CLI 复用 `scripts/run_strategy_lifecycle_v1.py`：

```text
lifecycle-preview --workspace-root <工作区> --bindings <配置JSON>
lifecycle-create --workspace-root <工作区> --bindings <配置JSON> --config <任务JSON>
lifecycle-start --workspace-root <工作区> --bindings <配置JSON> --job-id <任务>
lifecycle-status --workspace-root <工作区> --bindings <配置JSON> --job-id <任务>
lifecycle-pause --workspace-root <工作区> --bindings <配置JSON> --job-id <任务>
lifecycle-resume --workspace-root <工作区> --bindings <配置JSON> --job-id <任务>
lifecycle-tick --workspace-root <工作区> --bindings <配置JSON> --job-id <任务>
```

任务 JSON 包含 `job_id`、`binding_id`、`expires_at`、`max_calls`、`stages`、`trading_calendar`。阶段格式与有限调度文档一致。开始和恢复不启动后台进程，`tick` 最多推进一个阶段。

## 控制台与 AI 接口

构建前端后，通过以下方式启动只读页面：

```text
python scripts/run_ui.py 8000 --research-root <工作区> --lifecycle-config <工作区内的部署配置.json>
```

部署配置恰好包含 `{ "bindings": { ... } }`。页面地址为 `/research/lifecycle`，也可以点击控制台侧栏的“研究与观察进展”。工程能力、测试报告、实际观察和策略资格分别展示；任务、等待原因、来源及每日计划直接读取统一 API。默认只读，合成治理部署可在预览确认后创建单阶段任务或启动、暂停、恢复和推进已有任务。

启动器为 `BOUNDED_V2` 装配固定历史 loader：冻结 manifest 必须显式包含 `data_root`、`window`、`input_identity`、`profile=HISTORICAL_MODELED` 和 `candidate_capability=RESEARCH_RULE_STRATEGY_V2`。`data_root` 必须位于所选工作区内。读取复用 `build_bundle`，核对窗口及 `rule_input_identity`，不覆盖旧输入身份；配置文件本身不读取价格。现有 loader 仅支持该构造器可验证的数据窗口，工作区外的数据或其他股票池不会被静默回退为旧样本。

应用通过 `create_app(..., lifecycle_service=...)` 显式注入同一服务：

- `GET /api/research-lifecycle`：只读汇总，刷新不启动、恢复或写入。
- `POST /api/research-lifecycle/preview`：提交 `{action, payload}`，获得包含当前状态与部署绑定的 `preview_hash`。
- `POST /api/research-lifecycle/action`：提交 `{action, payload, preview_hash, confirmed:true}`，再次核验后执行。

支持 `create/start/pause/resume/tick`。写入沿用原本机请求限制与 `ExecutionPolicy`，保留明确确认和过期预览拒绝。原 Web 权限目前仅允许合成治理，因此真实任务在新 Web 入口仍为只读；显式 CLI 可以推进已有授权的真实任务。此接口不会扩大为真实远程控制权限。

## 恢复与未完成证据

Paper 在原提交后、调度回执写入前中断，可以通过原账户阶段链核对同一快照，完成调度结算而不重复入账。正式评审可以引用已经存在的 REPORT 或 FAILED。其他服务如果已经产生副作用却没有能够绑定到本操作的确定回执，会保持 `RECOVERY_REQUIRED`，不重新执行以求成功。

生命周期回执只引用原业务结果，不是新的资格或预算权威。源码变化仍阻止继续旧任务。无合格策略、尚未达到观察样本或不支持的统计范围都保持原等待和阻断。

自动化测试使用真实原 Paper/快照/账户实现及明确合成数据，验证 CLI/API 一致、重复触发不重复处理、提交后恢复、默认只读、确认与路径边界。真实采集和长期无人值守必须另列实际运行证据，不能用这些合成测试代替。

2026-09-27 接线验证：`test_lifecycle_service_v2.py` 的 8 项测试通过（49.06 秒）；规则与有限调度的 57 项测试亦通过。首次并行开发运行的 5 个接线夹具因 `BOUNDED_SOURCE_CHANGED` 阻断，原失败证据保留；在各工作者停止修改生产源码后，使用新测试目录重跑通过，没有关闭源码校验。两次报告中的重复测试不重复计数，共 65 项不同测试。

随后补充启动器和 loader 边界测试 3 项通过；前端展示测试连同原展示回归共 11 项通过，`npm run build` 通过。首次构建发现当前 TypeScript 目标不支持 `replaceAll`，改为等价的日期分隔符替换后通过。构建保留原整体包体积提醒，没有通过放宽编译选项或删断言消除问题。

CLI 与 UI 共用 `scripts/lifecycle_deployment_v2.py` 中的固定历史 loader；`BOUNDED_V2` 和 `DIAGNOSIS_V2` 都要求原 manifest 的数据根、窗口和输入身份。显式股票列表来自 `window.symbols`，传入原数据构造器，不会默认替换为两股。诊断会话的授权、预算及源身份仍读取其 `root/search` 原会话。

暂停是阶段之间的暂停：正在执行的阶段持有原变更锁，暂停请求返回忙，待本次阶段结束后可暂停后续阶段。它不强杀正在执行的模型调用或账户处理。部署不会启动常驻后台循环；每次 tick 最多推进一个有界阶段，未结算的 START 必须通过原服务核对恢复，不能当作免费重试。
