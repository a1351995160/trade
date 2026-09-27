# 带持有期与冷却期的 AI 研究循环

V2 允许 AI 在冻结目录里提出独立买卖条件、交易日持有期、冷却期和价格过滤规则。每个候选仍走公共策略入口、真实持仓状态账户、同一权威预算及试验记录；不会把状态规则交给旧的空持仓预计算账户。

## 使用入口

`BoundedResearchSessionV2` 位于 `research_factory/bounded_research_v2.py`。`create`、`scope`、`status`、`tick`、`run`、`revoke` 延续 V1 的调用形式；新会话版本是 `BOUNDED_RESEARCH_V2`，不能将旧冻结会话改标签恢复为 V2。

`input_manifest` 必须包含：

- `candidate_capability: RESEARCH_RULE_STRATEGY_V2`。
- `profile: HISTORICAL_MODELED`；测试可用 `SYNTHETIC`，不会被报告成真实数据。
- `input_identity`：由 `rule_input_identity(bundle, window)` 生成。
- `window`：证券列表、`feature_start`、`account_start`、`account_end`、显式 `calendar`。
- 可选冻结账户约束：`initial_cash`、`max_positions`、`max_symbol_exposure_bps`。
- 可选 `seed_failure_knowledge`：从既有 `seed_diagnostics(accounts_root)['feedback']` 取得，最多五份。只接受现有固定原因码、完全相同的定性文案及来源哈希，拒绝收益数值、任意文案和额外字段。整个反馈列表随会话冻结，不构成新的数据独立性或资格证据。

加载器签名保持 `loader(manifest, strategy) -> (bundle, events)`，bundle 使用 `RuleAccountBackendV2` 的历史输入合同，并包含相同 `input_identity`。日行情、换手率、证券状态和公司行动不得从旧 S1 预计算特征冒充。

模型使用既有 `BoundedCodexInvokerV1`；它按冻结能力输出 V1 投票 JSON 或 V2 表达式 JSON。V2 的模型 schema 和运行时解析器均限制算子、字段及参数；模型不能写 Python、改账户、读文件或调用工具。原 V1 默认能力和模型回执恢复语义保留。

## 诊断与反馈

新账户结果直接使用 `daily_accounts`、`fills`、`reconciliation` 和真实账户状态，不构造虚假的旧 `chain` 字段。诊断重算净收益、回撤、费用和成交数，区分账户证据异常与策略亏损。

下一候选只接收固定的定性成本、亏损和两个半段稳定性反馈。反复输出同一规则、输出不支持规则、模型中断都不会获得免费机会。档案继续复用原治理回执、预算、试验和撤销机制，新增规则只能取得探索档案，不能绕过正式方法适用性。

V2 参照账户是固定规则：价格为正时买入，最长持有 252 个交易日。它明确不是买入持有基准，也不是原 51 指标回归基线；原 V1 的 51 指标参照仍保留。正式统计评审必须使用自己的合格基准，不能把此参照替换进去。

## 正式评审交接

冻结后的 V2 档案可使用既有 `FormalAssessmentServiceV1.register/run/report`。V1、V2 家族不能混在一个冻结批次；同一权威目录继续记录家族、确认窗口和错误预算，不新建可重置的额度。

当前正式 V2 执行范围明确限制为原两只主板股票、100 万初始资金、最多两仓、单股 50%。研究入口允许的更大股票池不自动取得这个范围的统计适用证据；范围不同会在登记时明确拒绝，不能把原参数暗改成支持值。

正式数据通过 `build_rule_confirmation_bundle` 核验未来采集、开收盘原件及公司行动完整性。候选消费实际观察快照，普通与压力成本账户均使用状态引擎；基准仍是旧公共账户的真正等权买入持有。原基准尚不支持的公司行动范围会明确失败，不能静默漏掉事件。

`formal_rule_adapter_v2` 从新账户净值派生净收益与回撤符号，保持原结果形状；验证使用同一已消费账户的冻结授权进行确定性重放，并比较完整结果哈希。重放不会建立新候选或追加确认机会，但会耗费核验时间。

即使账户、统计计算和独立数据工程链都完成，当前 V2 仍保留 `REAL_RETURN_PROCESS_APPLICABILITY_NOT_ESTABLISHED` 与 `RULE_V2_METHOD_SCOPE_NOT_ESTABLISHED`。结果可以正式记录为拒绝或证据不足，不能被业务页面解释为获得 Paper 资格。

## 验证与范围

`test_bounded_rule_loop_v2.py` 覆盖合成输入经过公共真实执行引擎、生成两候选、重复规则消耗预算、档案重建、完成后恢复、模型中断拒绝免费重试及结果篡改。合成模型用例不代表真实 AI 调用；实际调用证据由后续批次独立记录。

这个入口每批最多五候选、两小时；没有新增无限自动研究、跨批预算扩容或正式资格。旧统计校准钉住的 `bounded_research_v1.py` 保持不变。

回滚应停止新 V2 会话或反向提交本次接线；保留已经发生的预算消费、试验、模型回执及账户结果，不能恢复成免费机会。
