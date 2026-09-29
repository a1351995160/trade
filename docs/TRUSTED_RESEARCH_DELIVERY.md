# 可信策略研究交付追踪

**截至本次编辑（2026-09-29），S1 两池真实固定账户工程验收已通过，但本计划及发布验收尚不能宣告全部完成。** 两池三规则五万元账户，加十万元对照，共 21 个账户、每个 122 个评价交易日，均完成独立核账。最新一轮测试为 252 项通过、1 项失败；失败已定位为 Windows DATA 阶段需要第三个受控 Git 进程，补丁已应用，修复后50项定向测试全部通过。前端 12 项通过、构建通过；发布仍待 CI。旧长集中回归已主动中止，不计作通过。真实账户工程通过不等于策略有效、独立验证或正式资格通过。

本表对应 [正式实施计划](plans/2026-09-29-001-feat-trusted-strategy-research-plan.md)，用于回答“现在能做什么、有什么证据、还差什么”。它不是新的授权、统计批准或发布声明。原计划、旧账户和研究记录不改写；2026-09-27 的旧版验收不能替代本次 V3 验收。

## 当前结论

| 事项 | 当前状态 | 对使用者的含义与证据 |
|---|---|---|
| 软件接线 | 公共链路已实现，S1 真实工程通过 | 三规则、两池、实际资金、成本/基准、退出和离线核账已有真实证据；其他阶段按各自条件验收，不自动获得业务资格。见下方 U1–U15。 |
| 自动测试 | 252 通过、1 失败；修复已应用，修复后50项定向测试全部通过 | Windows DATA 第三个受控 Git 进程问题已定位；复验结束前不写全绿。前端 12 项和 build 通过。旧长集中回归主动中止，既不是通过也不与本轮数字相加；发布待 CI。阶段记录见 [progress.md](../progress.md)。 |
| A 池真实固定账户 | **工程验收通过** | 三规则五万元及 MA10 十万元对照共四组、12 个账户完成；与 B 池汇总见 [真实结果](../reports/trusted_workflow_acceptance/REAL_ACCOUNT_SUMMARY.json)及[21账户独立复核](../reports/trusted_workflow_acceptance/REAL_REAUDIT.json)。 |
| B 池真实固定账户 | **工程验收通过** | 固定三股原件获取、资格检查和注册已完成，三规则正常/压力/基准共9账户核账通过。[B池资格](../reports/trusted_workflow_acceptance/QUALIFICATION_B.json)、[最新采集结果](../reports/trusted_workflow_acceptance/sources_B_connection_check_2/ACQUISITION_RESULT.json)。[首次失败](../reports/trusted_workflow_acceptance/sources_B/ACQUISITION_RESULT.json)仍保留，不代表当前阻塞。 |
| 方法适用性 | **当前研究方法不适用** | 五万元专用合成研究已完成可复查反例：在预冻的两点共同状态分布下，精确家族误报率 0.5，超过 0.025 门槛。不能把旧方法直接扩展到无相应过程约束的新账户范围。[方法报告](FORMAL_ACCOUNT_METHOD_V3.md)。 |
| 独立确认 | **等待方法及独立资料** | 已有完整家族、选择规则和未来窗口冻结接口；当前不能颁发 V3 确认资格。补抓历史不变成事前未见数据。[确认服务](../src/chanlun_trader/research_factory/campaign_confirmation_v1.py)。 |
| 真实模型接线 | 一次规则生成成功，费用上限未证明 | 仅为模型接线证据，未执行账户；费用 `UNKNOWN`、`cost_cap_verified=false`。不能冒充带可证明费用硬上限的无人值守研究验收。[原始核验](../reports/trusted_workflow_acceptance/model_smoke/VALIDATION.json)。 |
| Paper 与组合 | **资格等待** | 合成工程接线不增加真实观察日。当前没有本次已获真实资格的 V3 策略；真实 Paper、合格组合及自然日期观察仍等待。[操作说明](RESEARCH_LIFECYCLE_OPERATIONS.md)、[每日计划](TRUSTED_DAILY_PLAN_V1.md)。 |
| 完整交付、发布与接手 | **发布待 CI，未宣告全计划完成** | 两池 S1 与阶段边界恢复已有证据；Windows DATA 补丁定向复验、最终 CI 和未核实的操作者接手仍需逐项闭合。真实方法/Paper/组合资格等待单列，不要求无限寻找赢家。 |

A 池固定为 `000001.SZ / 000002.SZ / 600000.SH`，B 池固定为 `000006.SZ / 000733.SZ / 002036.SZ`。评价窗口固定为 2022-07-06 至 2022-12-30；两池各三份规则、正常成本/压力成本/买持基准，加 A 池一份 100,000 元对照，共 21 个账户作业已完成。每账户 122 个评价交易日，合计 2,562 个账户日，不能算作 2,562 个独立样本或真实 Paper 观察日。未更换盈利股票或修改窗口凑结果。完整设计见 [真实验收准备](TRUSTED_RESEARCH_ACCEPTANCE.md) 与 [冻结验收设计](../reports/trusted_workflow_acceptance/ACCEPTANCE_DESIGN.json)。

真实证据可从四个入口复查：[账户与退出摘要](../reports/trusted_workflow_acceptance/REAL_ACCOUNT_SUMMARY.json)、[再次独立核账与原件不变检查](../reports/trusted_workflow_acceptance/REAL_REAUDIT.json)、[版本绑定验收包](../reports/trusted_workflow_acceptance/PUBLISHED_ACCEPTANCE.json)、[恢复前后结果/结算/预算一致](../reports/trusted_workflow_acceptance/public_run/A_MA10_COST_STOP_50000_RECOVERY_CHECK.json)。[进程边界原件](../reports/trusted_workflow_acceptance/public_run/A_MA10_COST_STOP_50000_BOUNDARY_PROCESS_WITH_ENV.json)记录账户计算完成后、核验及报告前正常退出再恢复，不能夸大为所有故障点的真实强杀验收。`PUBLISHED_ACCEPTANCE.json` 是本地版本绑定验收包，不等于远端发布或 CI 已通过。

## 实施单元 U1–U15

“已有实现”只表示代码接线可查；该列不代表自动测试、真实运行或资格通过。测试文件链接表明覆盖落点，只有另列的实际运行结果才构成通过证据。

| 单元 | 面向用户的交付 | 软件与测试证据 | 当前证据与剩余条件 |
|---|---|---|---|
| U1 策略语义 | 指标实例与参数进入身份；摘要、持有期和退出配置有明确语义 | [V3 策略](../src/chanlun_trader/research_factory/research_rule_strategy_v3.py)、[规则测试](../tests/research_factory/test_research_rule_strategy_v3.py)；已有定向测试与两池真实规则证据 | 两池真实冻结规则与账户已对应；最新 Windows DATA 修复的定向复验及最终 CI 待闭合。 |
| U2 数据资格 | 按用途核对字段、日期与来源；未知不冒充真实 | [提供器](../src/chanlun_trader/research_factory/research_data_provider_v1.py)、[提供器测试](../tests/research_factory/test_research_data_provider_v1.py)；A/B资格已核，阶段记录 18 项通过 | A/B 输入资格均已核对并完成真实账户；历史可见性仍 MODELED、独立性 UNKNOWN，不升级独立资格。 |
| U3 公共提交 | 声明式策略经过预览、冻结、授权再执行，不能提交任意裁判代码 | [提交服务](../src/chanlun_trader/research_factory/strategy_submission_v1.py)、[公共 CLI](../scripts/run_trusted_research_v1.py)、[提交测试](../tests/research_factory/test_strategy_submission_v1.py) | 七组公共任务、21 账户真实链路已完成；发布版本 CI 与最新 DATA 补丁复验待闭合。 |
| U4 真实资金账户与基准 | 实际现金、整手、持仓限制、三类退出及全池买持基准 | [账户后端](../src/chanlun_trader/research_factory/rule_account_backend_v2.py)、[退出适配](../src/chanlun_trader/research_factory/rule_exit_adapter_v3.py)、[基准](../src/chanlun_trader/research_factory/research_benchmark_v1.py)、[退出测试](../tests/research_factory/test_rule_exit_integration_v3.py) | 五万元两池三规则及十万元对照全部完成；三类退出已有真实触发和核账证据，收益不构成策略有效性证明。 |
| U5 独立证据门与报告 | 绑定原输入、授权、执行、结算，再从成交独立重建账户 | [离线核验](../src/chanlun_trader/research_factory/research_evidence_v1.py)、[反例测试](../tests/research_factory/test_research_evidence_v1.py)；阶段记录修复后 22 项通过 | 21 个原 JOB 再次独立核账全 PASS；内存副本首日现金加1元被 DAILY_CASH_CONFLICT 拒绝，原件及预算不变。 |
| U6 实际宿主与恢复 | 无 AI 也可调度已有任务；中断不免费重跑、不重复结算 | [宿主](../src/chanlun_trader/research_factory/trusted_research_host_v1.py)、[生命周期](../src/chanlun_trader/research_factory/lifecycle_service_v2.py)、[宿主测试](../tests/research_factory/test_trusted_research_host_v1.py) | 真实阶段边界恢复已有结果/结算/预算一致证据；不宣称所有故障点均已真实演练，最终接手与 CI 待闭合。 |
| U7 人工与 AI 工作台 | 同一能力清单、预览与权限边界；用户无需写候选脚本 | [工作台](../frontend/src/console/components/LifecycleWorkbench.vue)、[Web 测试](../tests/research_factory/test_strategy_submission_web_v1.py)；前端12项及构建通过 | 前端12项和build通过；真实固定作业链已验，未单独核实的新操作者浏览器接手仍待记录。 |
| U8 固定流程与旧记录 | 固定两池三规则验收；旧 PASS 不自动晋级 | [验收方案](TRUSTED_RESEARCH_ACCEPTANCE.md)、[真实准备原件](../reports/trusted_workflow_acceptance/ACCEPTANCE_DESIGN.json) | **S1 真实工程通过：21账户 × 122日核账完成。** 不授予策略有效、独立验证、Paper或组合资格。 |
| U9 跨批总预算 | 总任务统一统计候选、数据、账户、模型与资源；改名不重置 | [预算服务](../src/chanlun_trader/research_factory/research_campaign_v1.py)、[预算测试](../tests/research_factory/test_research_campaign_v1.py)；阶段记录 18 项通过 | 预算实现与定向测试已有证据；实际有界自动研究仍受模型费用/token硬上限缺证约束。 |
| U10 持续研究与接手 | 保留候选来源、假说和诊断，宿主每次有界推进 | [研究服务](../src/chanlun_trader/research_factory/diagnosis_research_v3.py)、[研究测试](../tests/research_factory/test_diagnosis_research_v3.py)；阶段记录受限 worker 专项 9 项通过 | 一次真实模型接线成功；Windows DATA 第三个受控Git进程补丁修复后50项定向测试全部通过，真实跨批无人值守及换模型接手不冒充完成。 |
| U11 独立确认与暴露控制 | 确认前冻结完整家族、选择和未来窗口；方法等待不阻塞其他授权探索 | [确认服务](../src/chanlun_trader/research_factory/campaign_confirmation_v1.py)、[确认测试](../tests/research_factory/test_campaign_confirmation_v1.py) | 当前方法不支持，真正独立资料及正式确认仍等待；真实S1不改变该边界。 |
| U12 五万元方法研究 | 先冻结研究，再给出可核查的支持或不支持结论 | [研究说明](FORMAL_ACCOUNT_METHOD_V3.md)、[报告](../reports/formal_account_method_v3_20260929_run2/report.json)、[离线核账](../reports/formal_account_method_v3_20260929_run2/offline_audit.json)、[方法测试](../tests/research_factory/test_formal_rule_adapter_v3.py)；6 项通过 | 本方法 **UNSUPPORTED**；不证明所有其他方法均不可用，也不批准真实策略。 |
| U13 真实 Paper 接线 | 公共 V3 档案可进入受控工程观察；快照、撤销与恢复持久保留 | [公共档案](../src/chanlun_trader/research_factory/public_strategy_archive_v3.py)、[Paper](../src/chanlun_trader/research_factory/forward_paper_v1.py)、[跨入口测试](../tests/research_factory/test_public_strategy_archive_v3.py)、[操作说明](RESEARCH_LIFECYCLE_OPERATIONS.md) | 工程接线与测试保留；真实策略资格和自然观察日仍等待。旧临时隔离指纹排障结果不计最终验收。 |
| U14 组合与每日计划 | 共享现金、成员归属、独立组合资格；只读计划给数量与期限 | [组合资格](../src/chanlun_trader/research_factory/portfolio_qualification_v1.py)、[数量计划](../src/chanlun_trader/research_factory/trusted_daily_plan_v1.py)、[计划测试](../tests/research_factory/test_trusted_daily_plan_v1.py) | 工程计划与组合约束已实现；真实合格成员、组合独立资格和真实观察仍等待，不能继承S1资格。 |
| U15 整体验收与运维接手 | 汇总实际状态与阻塞，换操作者可识别原件、权限及下一步 | [跨阶段测试](../tests/research_factory/test_trusted_research_system_v1.py)、[运行说明](RESEARCH_LIFECYCLE_OPERATIONS.md)、本文 | S1、真实恢复、前端已具证；252通过/1失败的补丁定向复验及CI待闭合，未宣告全计划完成。 |

## 需求 R1–R26 追踪

此表检查“有没有明确负责处和剩余条件”，不以勾选需求代替业务验收。具体源文件与测试沿用上表对应单元的证据链接。

| 需求 | 当前实现落点 | 当前证据与剩余条件 |
|---|---|---|
| R1 人工与 AI 共用链路 | U1/U3/U7/U8/U10 公共提交、账户和核验入口 | 真实固定规则公共链路已验；真实AI自动研究仍受费用硬上限约束。 |
| R2 无 AI、无聊天也能运行 | U6/U7/U8 宿主、CLI 与工作台 | S1关闭AI运行及真实边界恢复已有证据；新操作者安装/接手未单独核实。 |
| R3 摘要与实际语义一致 | U1/U3 显式参数和规则身份 | 两池真实冻结规则和实际运行对应已具证；最终CI待确认。 |
| R4 不支持能力在消费前拒绝 | U1/U3/U9 能力预检与预算入口 | 预检与预算拒绝已实现；Windows DATA受控进程补丁修复后50项定向测试全部通过。 |
| R5 统一数据目录与用途资格 | U2/U3/U7 数据提供器和能力目录 | A/B资格均核对并用于真实账户；资格限于历史探索用途。 |
| R6 未知/推算/真实区分 | U1/U2/U4 实际依赖与数据资格 | 真实/推算/未知仍分开；两池账户通过不证明历史可见性或独立性。 |
| R7 冻结、版本、访问记录 | U2/U3/U5/U8/U11 原件绑定与确认协议 | 真实作业输入/版本/访问与恢复证据已保留；独立确认资料仍等待。 |
| R8 池与持仓分离 | U1/U3/U4 池范围、持仓和权重分别冻结 | 两池真实账户及分配已具证，池范围与持仓上限分别冻结。 |
| R9 实际资金和独立压力账户 | U4/U8 正常/压力账户及资金对照 | 21作业完成，包括正常/压力/基准及十万元对照；资金对照非简单比例缩放。 |
| R10 独立核账加绑定共同放行 | U5/U6 公共证据门 | 21原JOB离线复核全PASS，篡改内存副本被拒绝，原件和预算不变。 |
| R11 事前冻结基准与边界 | U3/U4/U5 全池买持基准及报告 | 事前基准、实际持仓与现金对照已随真实账户保存；不把日期一致解释为相同风险暴露。 |
| R12 方法按实际范围研究 | U5/U11/U12 V3 范围和研究报告 | 已有方法不支持结论，真实适用方法仍缺。 |
| R13 事实/假说/未知分开 | U5/U10 持久诊断、方法和费用状态 | S1收益只作工程结果；模型费用UNKNOWN和方法UNSUPPORTED分别保留。 |
| R14 六层结论分开展示 | U5/U7/U15 报告及本追踪表 | 真实账户工程已通过；自动测试结果、历史初筛、独立确认及正式资格分列。 |
| R15 统一累计资源 | U9/U10 总预算与受限 worker | 真实模型费用/token硬上限未证，自动任务继续拒绝无保障配置。 |
| R16 跨批授权不重置 | U6/U9/U10 事件和保留/结算记录 | 真实账户消费和恢复保留原预算；最新自动研究DATA补丁定向复验通过，最终CI待闭合。 |
| R17 候选父版本与换 AI 接手 | U10 持久研究状态和诊断 | 持久记录和测试已有；真实跨批换模型/操作者接手仍待相应实证。 |
| R18 中断不重复副作用 | U6/U9/U10/U13 持久阶段与恢复 | 真实边界恢复结果/结算/预算一致；不把该单一边界外推为所有故障点通过。 |
| R19 等待、暂停、失败分开 | U6/U9/U10/U11 状态机及跨阶段等待 | 阶段状态与等待隔离已实现；最新测试补丁修复后50项定向测试全部通过，旧长回归中止不计通过。 |
| R20 确认前冻结全家族 | U11/U12 未来确认协议与方法范围 | 无真实方法支持及独立确认结果，不允许缩小家族授资格。 |
| R21 封存权限与独立性 | U2/U11/U12 数据用途和暴露记录 | B真实原件已核并运行；补采和S1均不能证明历史独立性。 |
| R22 合格授权后真实 Paper | U6/U13 canonical 准入与自然快照 | 真实V3资格未取得，真实Paper自然观察继续等待。 |
| R23 组合共享资金与独立资格 | U14 原组合服务和数量计划 | 组合约束和数量计划已接线；真实组合资格仍等待。 |
| R24 用户/AI同权，无实盘下单 | U3/U7/U10/U13/U14 固定CLI和权限分派 | 公共链路及前端12项/build通过；无券商实盘授权，操作者接手单独核验。 |
| R25 同源版本化能力清单 | U1/U2/U3/U7/U8/U10/U15 [能力说明](RESEARCH_CAPABILITIES.md) 与生成器 | 真实验收已形成PUBLISHED_ACCEPTANCE版本绑定包；最终软件发布和CI仍等待，底层未接通能力不因此升级。 |
| R26 三类退出实际账户链 | U1/U4/U5/U6/U8/U13 分笔成本、日线确认与持久退出 | 三类退出均有真实触发与独立执行核验；真实Paper仍须取得资格和自然日证据。 |

## 验收场景 AE1–AE19 追踪

| 场景 | 软件/自动验证现状 | 真实证据现状 |
|---|---|---|
| AE1 关闭 AI 的三规则完整链路 | 公共CLI/宿主/三规则接线已落点，修复后50项定向测试全部通过 | 两池三规则五万元及十万元对照21账户完成，S1真实工程通过。 |
| AE2 MA10/MA20 身份与计算 | V3 规则与提交测试已有覆盖 | 七组真实任务的冻结规则、参数与账户已有对应原件。 |
| AE3 两池及池/持仓分离 | 多股账户与公共数据入口已有实现 | A/B数据资格和两池账户已通过；不再保留获取阻塞或待跑状态。 |
| AE4 数据未知与用途边界 | 数据资格、未知依赖拒绝已有实现/定向测试 | A/B探索账户输入已核；历史可见性MODELED、独立性UNKNOWN仍如实保留。 |
| AE5 逐项篡改和证据缺失 | 离线重建及反例测试已有运行记录 | 21原JOB独立复核PASS；首日cash加1元的内存篡改被拒，原件不改。 |
| AE6 全池基准与整手闲置 | 基准和现金账户实现、测试已落点 | 真实正常/压力/全池基准和资金对照完成，实际费用及收益不按资金简单倍增。 |
| AE7 两批研究、计数和换模型 | 统一预算/持续研究/接手测试已落点 | 一次真实模型接线成功；费用硬上限及跨批无人值守实证仍等待。 |
| AE8 中断恢复不重复 | 公共作业和 Paper 恢复已有测试；修复后50项定向测试全部通过 | A池MA10五万元阶段边界恢复已有原件；结果/结算/预算一致，未重跑账户。 |
| AE9 改名、入口、并发不扩权 | 预算与对象权限反例已有定向通过记录 | 真实作业授权、消费及恢复证据已具；自动研究费用硬限仍是独立边界。 |
| AE10 不适用等待不误阻探索 | V3 resolver、确认等待及跨阶段测试已落点 | 五万元方法研究结论UNSUPPORTED；S1成功不替代方法支持。 |
| AE11 全家族与确认暴露 | 确认协议冻结与完整家族测试已落点 | 完整家族冻结已有接口；真正独立资料、确认结果和相应访问证据仍等待。 |
| AE12 合成/迟到/漏采不能伪造天数 | 复用Paper自然时间、profile、重放门；修复后50项定向测试全部通过 | 无本次合格V3真实Paper；合成回放不计自然观察天数。 |
| AE13 共享资金、冲突与失格 | 原组合及新增只读数量计划已实现 | 工程组合/计划已接线；合格成员与组合独立资格未取得，真实观察等待。 |
| AE14 旧PASS不晋级 | 旧BS_档案与新PS_隔离、原件保留 | 本轮原件/预算复核前后不变；旧BS_/新PS_保持隔离，旧PASS不晋级。 |
| AE15 无聊天安装/恢复接手 | CLI、宿主及跨阶段测试已落点 | 真实公共链路和边界恢复已有证据；新操作者完整安装/接手及CI尚未核实结束。 |
| AE16 同源能力/文档/示例 | 生成式目录和查询入口已接通，阶段6项测试通过 | 真实S1能力与任务已有版本绑定包；AI模型smoke仍仅局部接线证据。 |
| AE17 过期能力不能绕过预检 | 快照、指纹与提交预检测试已落点 | PUBLISHED_ACCEPTANCE已绑定功能/源码/任务原件；最终发布及CI不据此自动通过。 |
| AE18 三退出配置进入真实账户 | 成本锚、最短持有、空仓和次日执行已有确定性测试 | 固定成本止损20、固定止盈14、移动止损12次真实触发评估已核，合并账户计数不是独立事件数。 |
| AE19 部分成交/分红/恢复/Paper | 分笔退出、公司行动核验、恢复与parity已落点，修复后50项定向测试全部通过 | 真实退出账户和阶段边界恢复已核；真实Paper自然日期及资格继续等待。 |

## 证据怎么读，后续还差什么

当前真实工程证据包括21个账户、每个122日独立核账，以及对同一21个原JOB的额外只读复核；复核不新增账户消费。正常/压力/基准分开运行，三类退出在真实窗口均有触发。方法研究另有六个504日合成账户，其结果不能混入上述真实账户数量或Paper天数。

最新测试记录为252项通过、1项失败。该失败已定位为Windows DATA阶段执行冻结时需要第三个受控Git进程，补丁应用后50项定向测试全部通过。前端12项通过、build通过。旧长集中回归主动中止，其部分进度不能写成完整通过，也不与本轮数字相加。早期方法6项、CLI4项等局部结果见[阶段日志](../progress.md)，不相加成总测试数。发布待最终CI；本地验收包并非发布成功凭证。

方法报告中的“不适用”针对这次被冻结的旧方法向新范围的推广，不是对所有方法的否定，也不是对真实市场误报率的估计。其反例过程不满足原理论的共同均值、近似独立正态前提；单个历史窗口看起来平稳或检验不显著，均不能补足这些前提。确认方法缺失时保留等待，不能靠修改范围常量或删除失败者解决。

当前剩余条件应分开处理：Windows DATA补丁定向复验已通过，最终CI尚未闭合，未单独核实的新操作者接手应补证；真实模型费用/token硬限未证明，不能宣称无人值守预算保障；真实适用方法、独立资料及资格不足，Paper/组合自然观察继续等待。A/B原件、资格、21账户运行和独立核账均已解决，不再列为待跑或数据阻塞。S1工程通过不消除上述独立条件，也不意味着必须无限寻找合格策略。

本文件只作当前快照，后续每个状态应以对应原件和最终测试结果更新，不删除本轮失败记录。回滚以 `2931cbde08cef9c81bbd9807a79eef12ad1259ab` 为软件基线：停止新增派发，回退软件版本；保留已冻结的输入、已消费预算、模型回执、账户、方法协议、Paper阶段与撤销事实。不得删除账本来获得新的研究机会。

## 发布前最终补充

Windows DATA进程配额修复后，连续两批研究、实际新建venv、资源限制、宿主及发布凭证共50项测试全部通过。原252项通过／1项失败的记录保留为修复前结果，不将其称为全绿。前端12项测试及最终构建通过；完整Windows／Linux回归由PR CI负责，合并前必须通过。

真实账户原件共565项已按原字节打包为[REAL_ACCOUNT_ORIGINALS.zip](../reports/trusted_workflow_acceptance/REAL_ACCOUNT_ORIGINALS.zip)，[清单](../reports/trusted_workflow_acceptance/REAL_ACCOUNT_ORIGINALS_MANIFEST.json)包含包哈希。包中保留原绝对路径绑定，不伪称任意目录解压即可重放；同机原件仍保留，跨机先核对部署与原件身份。便携发布元数据可通过 `python scripts/run_trusted_research_v1.py publication` 查询，不能把发布凭证视为策略资格。
