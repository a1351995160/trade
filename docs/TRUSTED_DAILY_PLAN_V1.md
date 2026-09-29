# 可信每日数量计划

公共只读函数：`chanlun_trader.research_factory.trusted_daily_plan_v1.trusted_daily_plan(session)`。参数为既有 `ForwardPaperSessionV1` 实例，部署方负责绑定原 Paper 根目录；不能由用户提交账本或资格布尔值。

函数核对 Paper header、记录哈希链并通过 `_recover` 重建账户，读取当前权威成员及组合资格。只有最后一份完整 CLOSE 原件中的下一交易日计划可用；OPEN阶段不会重复显示已执行的旧计划。不会调用 ingest、提交订单、修复 HEAD 或写入计划文件。输入原件有冲突时沿用原服务异常，调用方应显示阻塞，不能忽略错误后展示旧建议。

返回 `intents` 包含证券、方向、策略、数量、原始原因、中文说明、最近收盘参考价与买入预计现金；同时给出可用现金、预计买入资金、被排除意图、有效交易日和截止时间，以及 header、记录、输入、原计划和当前资格凭证。最终数量计划有独立 `plan_identity`。

买入数量复用 `PortfolioExecutionRiskV1.buy_quantity`，依原计划顺序以虚拟待处理订单共享现金、同股上限、策略权重、持仓数量、费用及换手限额；不预支卖出款。卖出复用归属批次和T+1可卖数量。仅为最近收盘价下的数量估算，次日开盘仍须重新核验价格、费用、停牌、涨跌停、资格和其他成交约束，不保证成交。

截止时间取下一交易日开盘加原 `open_delay_minutes` 与组合政策到期时间的较早者。到期、观察撤销、缺少收盘数据、所有成员失格均返回无交易状态；正式观察还必须有原 header 绑定且当前有效的组合资格，不能继承成员资格。工程观察可生成估算，但 `portfolio_qualified` 和 `real_execution_authorized` 不因此变真；本接口不含券商实盘操作。

## 验证与回滚

针对性测试位于 `tests/research_factory/test_trusted_daily_plan_v1.py`，覆盖共享现金和同股限额、不改Paper文件、过期、成员/观察撤销、缺数据、OPEN后不复用旧计划及成员资格不能传递给组合。首次测试在共享源码同时变更期间被既有 `BOUNDED_SOURCE_CHANGED` 守卫正确阻断；须源码稳定后重新运行，不能据此宣称通过。

运行：`python -m pytest tests/research_factory/test_trusted_daily_plan_v1.py -q -p no:cacheprovider --basetemp=.test-tmp/trusted-daily-plan-final`。

回滚：停止调用新函数并移除新增模块、测试和本文；无需修改或删除任何 Paper 原件、授权、账本或预算。
