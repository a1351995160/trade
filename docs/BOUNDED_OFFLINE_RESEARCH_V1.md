# 有限真实离线研究 V1：核验与恢复记录

## 当前业务结论

已完成实际 main、冻结政策、真实因子定义和指定输入 schema 核验；真实试验不能启动。当前为 `BLOCKED_DATA_OR_POLICY`，不是策略失败，也未完成总目标。新增工程仅为受限元数据检查，没有授予运行许可。任务来源是当前用户消息对附件范围的明确认可；附件本身不是逐项批准回执。

源码根：`E:/llmwiki/bounded-offline-strategy-research-v1`；分支：`codex/bounded-offline-strategy-research-v1`；实际远端 main：`fed519bf8cdee61b6fc0c0ee5fe2483bf9a9fa82`。独立输出根创建前不存在，现为 `E:/llmwiki/autonomous-strategy-research-v1`。原输入根仅作限定只读来源。原根与新 main 均无 AGENTS.md，采用本会话提供的规范及新 main 的 CLAUDE.md。

## 已绑定的事实

- V2 政策及 lock 已由原 `load_validation_decision_policy_v2` 校验；内容身份 `93c97ef9d8b88f204d179a01301276d4142f13046e72192134d18db3a4834744`，政策文件 SHA256 `d340a9e249dfaf2f6c0a058a2c305525146ae24343e46a8cc1f2ecb67bcb1e39`。
- 原 registry 文件 SHA256 `921ac0184e09029d6eff8ec2888927ebc353be2778288a547c4427f0ea459883`。RETURN_5D 是 close 的 5 期 pct_change，预热 6，T_CLOSE，feature_price_mode=RETURN_ONLY；当前 caller 只接受 RAW。未改写定义。
- 冻结研究窗口 20220801..20250731。源码配置训练 20220801..20240731、Validation 20240801..20250731，Final Test 从 20250801 开始。必要预热日期须从获准的冻结日历取，尚未读取混合日期 JSON。
- `docs/VALIDATION_REUSE_POLICY.md` 记载既有 Validation 访问 23 次、18 个因子的 Validation 已见。原区间不能重新声称独立样本外；新 lineage 至多一次正式验证，开发要求训练内 purged/anchored walk-forward、bootstrap 和 placebo。
- 首轮上限：30 候选、40 次绩效曝光、其中至多 5 个验证候选；并发 1、不自动重试；研究计算累计 12 小时、确认后 72 小时；外部模型及付费数据 0。均须与 canonical 预算取更严格者。具体计划尚未确认，programme ID、预算桶与委托回执未创建。工具记录本次目标建立时间为 2026-09-11T10:05:03+08:00，暂以 2026-09-14T10:05:03+08:00 为外层授权截止投影；实际批准来源有更早时间时从严校正。后续计划确认不自动延长外层授权。

## 实际输入核验

只全文读取附件批准的三个纯元数据 JSON；两个 Parquet 只读 schema/footer，不读取行情/因子数据行，不计算混合文件全文哈希。

| 输入 | 实际结果 | 影响 |
|---|---|---|
| data/research/daily_all.parquet | 缺 available_at、volume_unit、amount_unit、price_mode | caller 不能证明时点、单位和价格模式 |
| data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet | 缺 available_at | 不能复用缓存为正式因子证据 |
| security_state/raw/trade_calendar.json | 已确认文件存在，未读内容 | 混合日期边界须先有合法分片方案 |
| security_state/normalized/security_master_v2/records.json | 已确认文件存在，未读内容 | 不先整读再裁剪历史主表 |
| security_state/normalized/pit_universe_v2 | 已定位逐日文件布局，未读取行 | 可按批准日期选择文件；来源和公司行动仍未核验 |

路由纯元数据明确日线原来源为 `E:/new_tdx_mock/vipdoc/*/lday/*.day`，属于输入根外，本轮未访问。来源核验需要该来源的受限访问许可或可追溯的窗口内快照；不能补造 available_at、NORMAL/TRADING 或 KNOWN_NONE。

## 事前验收与未决政策

沿用 V2：所有数据/PIT/执行/治理硬门禁；至少 30 笔平仓且 bootstrap COMPLETE；BASE 净收益 >0、PF 为 None 或 >1、COMBINED_X2 净收益 >0、raw p<0.05，冻结家族 BH q=0.05；最终由 FinalResearchAdjudicatorV1 裁决。回撤/集中度等按原软证据报告，不新增收益门槛。BASE 100 万、小资金 1 万、3 仓、100 股；原费用及成交规则保持。

实际 corrected bootstrap 对单笔盈亏独立有放回抽样并累计，未绑定时间相关/重叠持仓处理。V2 家族要求绩效前固定成员，和开放式新候选委托如何共同冻结尚无批准合同。现有“raw p 值”不能仅因名称就当已证明适用于自主多轮选择的正式推断。修改统计方法或家族处理属于明确版本的政策变更，不作为工程修错。

## 最小待补清单

1. 指定既有五份设计交付的精确路径，以及应沿用的 Objective/预算身份；允许对 `data/research/research_factory` 内对应授权、预算、比较集和家族记录生成只含身份/消费/成员/曝光计数的盲化投影。不得把不存在的历史当空历史，设计侧不读取精确 p 值或收益。原 Trial/报告区不做泛扫。
2. 提供可追溯、窗口内且带时点/单位/价格/公司行动来源的输入快照；或明确允许核验上面的根外 TDX 原始来源及其来源元数据。许可不能替代缺失的历史证据，无法证明的字段继续 BLOCKED。
3. 在任何绩效前明确统计适用性合同：重叠持仓和序列相关如何处理、训练内 walk-forward/隔离间隔与 placebo、开放候选流如何维持事前冻结家族及历史负担、已见 Validation 可支持的最高资格。建议先批准制定此窄范围方法合同，再按原阈值冻结；本轮未自行更换方法。

worker 单次时间/内存上限未发现可沿用的已批准运行值，不套用合成 demo 配置。上述来源确定后，以机器能力与合法配置更严格者写入一次性具体计划确认。没有这些事实时不伪造“可执行计划已冻结”。

## 下一合法动作与工程范围

补齐上述身份/来源/政策后，继续同一任务：先对账预算和曝光，建立一次性 USER_RESEARCH_PROGRAM_DELEGATION；复用原 Objective、预算、Trial、Structural、新颖性、runner 与最终裁决。新版本默认拒绝，撤销/跨域/预算/恢复均需回归；不得解除旧 synthetic 或 Phase2 域检查。实际数据缺少证据期间，不预先实现无法认证的运行授权服务。

元数据核验可复现命令（PowerShell，在独立源码根）：

```powershell
$env:PYTHONPATH = 'E:/llmwiki/bounded-offline-strategy-research-v1/src'
.venv/Scripts/python.exe scripts/inspect_bounded_research_metadata.py
```

该命令只输出诊断 JSON，不写原根，不运行研究。原始结果及回归日志在独立输出根。Windows L1/L6 的 OPEN_ROOT_CAUSE_UNCONFIRMED 保留，本轮未扩大 skip、timeout 或白名单。真实候选冻结/试验/验证消费均为本轮 0；canonical 剩余额度 UNKNOWN；Codex 桌面额度未量化，不宣称为零。

回滚：对本轮提交执行 git revert，停止使用本诊断入口；独立证据保留。未修改输入数据或历史账本，无数据回滚动作。
