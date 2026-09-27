# 数据资格与已用数据清单

这项服务回答“现有资料缺什么、哪些窗口已进入研究”，不会单凭文件清单颁发独立验证资格。

## 业务规则

- 相同输入身份、相同内容哈希，即使放进新目录，也属于同一份已用资料。
- 相同证券的日期窗口有重叠，即使换供应商或复权方式，也视为相关暴露。预热窗口也计入。
- 查不到访问记录只能写“未知”，不能写“从未看过”。人为填写 `UNEXPOSED` 不会升级资格。
- 单位不完整不进入研究元数据准备状态；证券状态、公司行动或可见时间未说明，不进入账户元数据准备状态。
- 保留新股、退市证券及其他原始清单字段，不按今天可交易名单删除历史成员。
- 数据抓取时间、文件修改时间、当年可见时间是不同概念，不互相替代。

## 可调用接口

模块：`chanlun_trader.research_factory.research_data_qualification_v1`。

`inventory_metadata(roots)` 只读取指定目录直接子项的名称、大小、修改时间；不打开行情、收益或封存文件。目录不存在、无权限或重定向会明确记录。

`read_governance_exposures(root)` 读取既有 `StrategyBatchGovernanceV1` 的 `CONFIRMATION.json` 和对应 `*_START.json`，验证回执哈希、计划哈希及开始记录绑定。返回原回执、开始记录、预算路径和目标的引用，不读取账户结果，不创建或改变预算。仅有批准但没有 START 的资料仍标为可能暴露。这个接口覆盖该治理来源，不声称覆盖旧系统全部访问记录；其他来源可通过显式曝光记录加入，未覆盖部分保持未知。

`audit_data_qualification(manifests, exposure_records)` 接受以下元数据列表：

```python
manifests = [{
    'dataset_id': 'TDX_DAILY', 'paths': ['供应商原文件路径'],
    'content_hash': '已有来源清单中的内容哈希',
    'input_identity': '已有研究输入身份',
    'source': 'TDX', 'captured_at': '2026-09-27T01:00:00+00:00',
    'symbols': ['000001.SZ'],
    'window': {'start': 20220407, 'end': 20240731},
    'fields': ['close', 'volume'],
    'units': {'close': 'CNY', 'volume': 'SHARES'},
    'adjustment': 'RAW', 'state_basis': 'HISTORICAL_MODELED',
    'corporate_action_basis': '已有公司行动来源描述',
    'availability_basis': 'MODELED',
}]
```

曝光记录包含 `input_identity` / `content_hash`、`symbols`、`window`、`purpose`、`evidence_ref`。证券窗口重叠识别独立于文件路径与复权声明。

报告逐数据集给出 `metadata_research_ready`、`metadata_account_ready`、`historical_independence`、原因、匹配的原始记录引用和报告哈希。前两个状态只表示元数据声明足够进入下一层核验。`source_authenticated` 与 `independent_confirmation_eligible` 始终为 `false`：供应商数据真实性、逐日完整性、正式协议、账户与统计资格仍由相应业务服务验证。该审计是只读投影，不是新权威账本。

## 验证与限制

对应测试覆盖异目录相同内容、不同复权同窗口、未知独立性、单位/状态缺失、新股和退市保留、真实治理字段投影、回执篡改及不读封存文件。运行：

```powershell
$env:PYTHONPATH = 'src'
python -m pytest tests/research_factory/test_research_data_qualification_v1.py -q
```

真实目录审计报告由实施批次统一保存。自动化用例通过只证明这些规则的实现，不证明存在可用封存独立数据。

回滚可停用这个只读审计入口或反向提交其新增文件；不得删除原治理回执、预算和暴露事实。
