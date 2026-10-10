# TRAIN 资料物理投影

维护者使用 `scripts/project_research_train_v1.py`，在已批准的准备范围内把父资料投影到新目录。它不修改父原件，不补采行情，不生成独立验证资格；普通探索登记子目录后不重新读取父行情。

固定投影配置由维护者保护，可信部署保存它的 SHA256。CLI 只能选择已登记的 `dataset_id`，不能传入父行情路径、Python 模块或 resolver：

```json
{
  "schema_version": "TRUSTED_TRAIN_PROJECTION_DEPLOYMENT_V1",
  "trusted_data_deployment": {
    "path": "E:/protected/data-access-deployment.json",
    "sha256": "<维护者固定的64位SHA256>"
  },
  "registered_parents": {
    "registered_parent": {
      "root": "E:/registered/parent",
      "manifest_path": "manifest.json",
      "manifest_sha256": "<已登记元数据原件的64位SHA256>"
    }
  }
}
```

`trusted_data_deployment` 指向 `TRUSTED_RESEARCH_DATA_DEPLOYMENT_V1` 配置，其 `records` 每项固定授权文件的绝对路径、SHA256 及 `OwnerApprovalStoreV1` 批准引用。完整授权内容须使用 `TRUSTED_RESEARCH_DATA_ACCESS_AUTHORIZATION_V1`，用途为 `DATASET_TRAIN_PROJECTION`，精确绑定父 dataset/manifest SHA、全部来源 SHA 和真实物理范围、输出范围、`RESEARCH_DATASET_PROJECTION_V1` 配方、输入/输出字节上限和到期时间。配置 pin 或授权引用本身不代替 owner 批准。

Parquet 在完整哈希前检查真实 footer 日期。JSON 没有独立的日期 footer：owner 必须针对该精确 SHA 审核整文件的真实物理范围，不能把 manifest 自报范围视为证明。只有批准覆盖完整原件后才能读取或哈希；运行时仍检查实际内容范围、撤销、到期及文件变化。范围未覆盖则阻断，不自动放宽封存锁。

示例仅展示命令形状，需替换成部署中实际登记且批准的引用、日期和 pin：

```powershell
python scripts/project_research_train_v1.py `
  --deployment-config E:/protected/train-projection.json `
  --deployment-sha256 <固定配置SHA256> `
  --dataset-id registered_parent `
  --authorization-ref approved_train_preparation `
  --output-root E:/registered/train_child `
  --train-start 2022-09-02 --train-end 2024-12-31 `
  --account-start 2023-03-01
```

目标目录必须尚不存在，父目录必须存在。成功返回 `manifest_path` 和 `receipt_path`；将子 `manifest_train_v1.json` 按既有 `UniverseDataProviderV1` 部署登记。价格、状态、参考价、日历和覆盖按各自日期语义投影；完整证券分母、来源单位和父哈希保留。跨窗权利条款保持原 `available_at`，封存范围中的条款没有独立具体权限时阻断。

回执中的 `data_dependencies` 披露真实账户交易日和缺口：504 天探索资料、独立 252 天及预热/状态/公司行动等伴随资料不足时仍需补齐真实依赖。投影不能把价格扩展冒充完整账户资料，也不能将合成测试或短窗工程通过作为业务验收。

合同采用未来 `EXPLORATION` 路由时，投影完成还需独立的 Owner 入场批准。维护者准备下列完整 JSON，由部署已保护的 `OwnerApprovalStoreV1` 批准；生命周期固定登记同时保存 `exploration_admission={path,sha256,approval_ref}` 和 `train_projection_deployment={path,sha256}`，普通请求不能自行提供或替换它们：

```json
{
  "schema_version": "OWNER_TRAIN_PROJECTION_ADMISSION_V1",
  "research_contract_hash": "<已批准合同的content_hash>",
  "trusted_route": {
    "route_id": "registered_train_route",
    "producer_identity": "registered_train_producer",
    "source_ids": ["<合同登记的完整来源ID集合>"],
    "start": "2022-09-02", "end": "2024-12-31", "purpose": "TRAIN",
    "universe_hash": "<合同完整证券身份>",
    "quality_policy": {"<合同质量政策键>": "<合同质量政策值>"}
  },
  "dataset_id": "registered_train_child",
  "manifest_sha256": "<子manifest的SHA256>",
  "receipt_sha256": "<PROJECTION_RECEIPT.json的SHA256>",
  "projection_deployment": {"path": "E:/protected/train-projection.json", "sha256": "<固定配置SHA256>"},
  "authorization_ref": "approved_train_preparation",
  "window": {"feature_start": "2022-09-02", "account_start": "2023-03-01", "account_end": "2024-12-31"}
}
```

三个日期须与原 CLI 的 `--train-start`、`--account-start`、`--train-end` 一致。`trusted_route` 精确包含 `route_id`、`producer_identity`、完整 `source_ids`、`start`、`end`、`purpose="TRAIN"`、`universe_hash` 和 `quality_policy` 对象。来源集合和证券身份必须匹配实际登记子资料；生产者与质量政策归属由完整 Owner 批准绑定，不能靠 manifest 自报成为可信路线。

实际候选可以在这一完整入场窗口内申请子窗口：`feature_start` 不早于入场首日，`account_start` 不早于入场账户首日，`account_end` 不晚于入场末日，并继续满足预热和正式资格要求。这样可以先做初筛，再在同一已批准投影内扩大到 504 天；不能改写原回执、入场批准或 manifest。准备 worker 的原件读取授权仍须覆盖整个子资料文件，输出账户窗口则使用本次合法子窗口。

固定 builder 在正式准入时调用 `train_projection_admission_v1.verify_train_projection_admission(...)`，复核入场批准、原准备批准、固定投影部署、当前配方 SHA、子 manifest/receipt、来源单位继承和完整分母，不重开父资料，也不读取子行情。返回的 `VERIFIED_TRAIN_PROJECTION_ADMISSION_V1` 只是绑定证明：`account_data_ready=False`，仍须完成原 bounded provider 的内容哈希与账户资格检查。它不创造历史独立性，也不把 `quality_policy` 内的声明转换成已通过的资格。

回滚仅需停止登记该新子目录并移除该投影输出；父原件和批准记录保持不变。已有登记任务应先完成对账，不能改写其冻结输入。
