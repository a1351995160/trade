# 公司行动跨入口接入

现金分红继续使用原 `CorporateActionAccountingV1` 或 `IndividualDividendAccountingV1`，没有第二套资产账本。新增接收适配器只负责在登记日前接入有证据的事件、生成回执和拒绝修改历史。历史回放与实时观察的证据分别解释。

## 接入约定

1. 创建观察账户时冻结税制。个人递延扣税使用 `IndividualDividendAccountingV1`，固定显式净额等限定用途使用原公司行动账本。同步给 broker/risk 同一 ledger；禁止中途换账本。
2. 采集方保存原始来源文件，核对 `source_sha256`，提供 `CorporateActionEnvelopeV1`：`mode=OBSERVED`、`revision=1`、`provider`、`source_ref`、`source_sha256`、带时区的 `received_at` 以及原事件 `event`。事件含公告发布时间、登记日、除息日、付款日、每股金额、单位和有来源税规则。适配器核验这些字段及时间顺序，但不认证调用者声明的来源真伪。
3. 在已有运行锁和阶段事务内，调用 `accept_action(ledger, envelope, asof=实际处理时间)`。必须在登记日 15:00 前收到并处理；已越过登记状态的迟到事件不允许回填。调用者负责证券范围、来源授权和完整覆盖核验，不能以没有收到事件或 `TodayDRFlag=false` 断言无事件。
4. OPEN 成交前调用原 `ledger.on_open(ts)`；CLOSE 更新持仓后调用原 `ledger.on_close(ts)`。应收款计入权益但不计入可用现金；到账只发生一次。特征视图只纳入已接收且已生效的事件，继续调用原 `causal_hfq_bars`；成交保留未复权价格。
5. 将事件列表与 ledger checkpoint 在同一既有阶段提交中保存。checkpoint 已包含接收回执，restore 仍使用原类和原 dataset_id；恢复后相同封套重投返回原回执，不新增派息。阶段提交前崩溃按原流程恢复并重放，不允许单独保存事件列表却丢弃账户状态。
6. `action_reconciliation(ledger)` 输出登记数量、应收、到账、税款、审计记录及 checkpoint 绑定供独立核账。它是核账输入，不是“核账通过”结论。

## 严格限制

- 同事件 ID 的任何不同封套、修订版本或 supersedes 均拒绝，需要人工/已有工程恢复流程核对，不更改过去交易、登记权益和决策。
- 动态入口本版仅支持现金分红。送股、转增、拆并股在原历史引擎的限定支持不等于实时入口已支持；原个人税制也只接受现金分红。
- 个人税制沿用 `DEFERRED_INDIVIDUAL_2015_101` 实现；登记之后、支付之前卖出享有红利的股份仍会拒绝，不能声称所有分红过程均可连续运行。迟到到账证据和复杂税规则也不能静默推定。
- 接收错误由调用入口持久化为阶段阻断；适配器自身不变更现金和持仓，也不替调用者继续成交。
- 已核对的 BaoStock 历史文件 `DIVIDEND_000001.SZ_2023.json` 明示 `historical_available_at_verified=false`、`mode=HISTORICAL_MODELED`；不能将下载时间改称当年接收时间。本模块没有取得实时公司行动实测证据。

新增测试使用清楚标记的合成来源，覆盖登记、应收、到账、个人税、提交前后恢复、重复与更正拒绝、迟到和缺条款。真实未来观察结果应由实际采集与运行报告另列。

## Paper 显式新模式

创建会话时指定 `company_actions='OBSERVED_CASH_DIVIDEND_V1'` 才启用动态个人现金分红；默认旧模式保持遇事件停止。新模式每个阶段都需要 `corporate_action_envelopes`（可以为空）和 `corporate_action_coverage`，`corporate_actions` 必须逐项等于封套内 event。阶段记录持久化完整封套，已有封套重复出现必须完全一致。

覆盖记录包含 `profile`、`provider`、`received_at`、`market_date`、完整 `symbols`、`complete=true`、按序 `envelope_hashes`，以及绝对原件路径 `source_ref` 与文件字节 SHA256 `source_sha256`。覆盖原件 JSON 必须等于覆盖记录除路径/hash之外的所有字段。事件原件 JSON 必须等于封套中的 `{provider, received_at, event}`。系统实际读取文件核对 hash 和内容，文件缺失、修改、覆盖不全、跨日或错证券均停止会话并保留原因。新 Paper 只接受原个人递延税规则，不将税率推定为零。

来源文档用于留存供应商/采集方证据，不是交易所数字签名认证；写一个自称真实的 JSON 文件不会自动建立来源可信性，采集边界仍须管理可接受供应商。现有 TDX 每日报价标志不提供这份完整证据时，不能启用该模式冒充可连续实时分红。

除息日的原始昨收核验使用已接收事件中的每股红利调整，其他日仍要求价格参考连续；这不更改成交价格。未来生效的事件先保留在原账本，到生效日才进入因果指标视图。

## 独立验证 V2 证据入口

`formal_evidence_v2.build_rule_confirmation_bundle` 沿用原证据构造参数，显式输出 `OBSERVED_CASH_DIVIDEND_V1`，不修改 V1 的无事件协议。按实际接收时间顺序核验每个 OPEN/CLOSE 的覆盖原件和公告封套，并保留原冻结后采集、专用目录完整库存、交易所日历、阶段时间、开收盘参考价约束。真实 TDX 快照继续从每条原始请求重建价格、状态和换手率；只有完整已核验且当日生效的现金事件可以解释当日除权标志，封套不能覆盖行情字段。

返回的 events 沿用原会计事件 schema，未来未生效公告仍绑定证据身份，但不提前进入行情调整。输出 profile 明确区分合成与实际采集；构造成功不授予策略资格。下游旧买持基准仍有付款日必须等于除息日等限制，入口支持完整事件证据不意味着旧执行器所有情况都可运行，必须保留其拒绝结果。新增六项证据测试使用合成来源或模拟原始 TDX 响应，不代表真实市场采集验收。

保存结果含现金分红时，`formal_cash_benchmark_v2.validate_cash_benchmark` 使用独立版本的核验适配。基准仍是原 `prepare_formal_account(None)` / `run_formal_account(None)` 真正等权买入并持有，不换成有限持有期规则。验证必须传入同一已消费授权，通过原公共账户重放及原 `run_chain` 独立红利核账，再核对订单、成交、逐日现金与权益、收益、费用、红利权益及审计轨迹；不新增研究机会、预算或资格。旧无公司行动验证器保持不变。四项合成测试验证真实买持行为、暖期权益隔离、保存红利收入篡改拒绝、原授权和买持身份约束。
