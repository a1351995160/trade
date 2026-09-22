# 回测行为验收 V1（BT_BEHAVIOR_ACCEPTANCE_V1）

本轮要回答的问题：**现在能用哪个入口、做哪些指标与退出方式的研究；它准确执行的是什么规则；
还有什么不能信？**

本文件只描述本轮经真实执行证据验证的范围。它不是收益结论，也不代表获得真实研究资格。

---

## 1. 入口映射（本轮实际核清的调用关系）

| 入口 | 代码位置 | 引擎 | 参数与退出 | 本轮状态 |
| --- | --- | --- | --- | --- |
| 经典 Web 回测 `POST /api/backtest` | `src/chanlun_trader/webapp.py` | 旧 `BacktestRunner`（`src/chanlun_trader/backtest.py`） | 缠论笔/中枢买卖点；`stop_loss_pct` 作用于**信号结构低点**；可选移动止损（最高收盘价） | **LEGACY，未认证**。在 `create_app` 下仍返回 `LEGACY_EXECUTION_DISABLED`；本轮未改其策略与默认行为 |
| CLI `scripts/run_backtest.py` | 同上 | 旧 `BacktestRunner` | 同上 | LEGACY，未认证 |
| 研究层 `research/backtest.py::run_v2_daily` | `src/chanlun_trader/research/backtest.py` | `BacktestEngineV2` | 固定持有（`max_holding_days`）+ 外部 `exit_fn` | 既有研究链路，未改 |
| 研究工厂 `real_runtime.py` / `predictive_executor.py` | `src/chanlun_trader/research_factory/` | `BacktestEngineV2` + `PortfolioExitEvaluatorV1` | 冻结合同的 `FIXED_HOLD` / `STRUCTURE_INVALIDATION` | 既有，未改 |
| **本轮新增：`POST /api/backtest/behavior`** | `src/chanlun_trader/webapp.py` | `BacktestEngineV2` | `BT_DAILY_EXIT_V1` 四类退出 | **已验证**（只读计算，需显式 `allow_readonly_compute=True`） |
| **本轮新增：`scripts/run_behavior_backtest_v1.py`** | `scripts/` | 同上，**同一服务函数** | 同上，支持 CSV/Parquet 文件入口 | **已验证** |

新入口与 CLI 都调用 `chanlun_trader.engine.behavior_service_v1.run_behavior_backtest_v1`，
因此同一合成请求在 API 与 CLI 上给出相同的语义事件与账户结果（已有测试锁定）。

关键区分：

- **旧入口的结构止损语义未被改动**，也没有被偷偷改成成本止损；
- **旧入口大盘过滤的开盘前视缺陷已修复**（见 §4），但旧入口整体仍标记为 legacy / 未认证；
- 配置里有止损字段**不等于**执行层使用了它。旧入口的 `stop_loss_pct` 作用对象是信号结构低点，
  与成交成本无关；新入口的成本止损才是以实际成交价为锚。

---

## 2. 规则口径（冻结）

### 2.1 指标（`BT_INDICATORS_V1`）

**MACD_V1**（`src/chanlun_trader/engine/indicators_v1.py`）

- `EMA_fast` / `EMA_slow` 显式递推，默认 `fast=12, slow=26, signal=9`；
- 段内首值 = 该段首个合格 `close`；`DIF = EMA_fast - EMA_slow`；`DEA = EMA_signal(DIF)`；
- `HIST = 2 * (DIF - DEA)`；
- `ready` 要求段内连续合格输入数 ≥ `slow + signal - 1 = 34`。

该 warmup 口径是**本实现选定**的工程约定，不声称与任意客户端初始化完全相同。

**KDJ_V1**（KDJ(9,3,3)）

- `LLV` / `HHV` = 段内最近 N 根（默认 9）合格 bar 的 low 最小值 / high 最大值；
- `RSV = 100 * (close - LLV) / (HHV - LLV)`；`K = (2K' + RSV)/3`；`D = (2D' + K)/3`；`J = 3K - 2D`；
- 段首 `K' = D' = 50`；满 N 根后才输出第一条有效结果；`J` 不裁剪到 `[0, 100]`。

**分段语义**：任一不合格输入（NaN / 非有限 / 价格 ≤ 0 / `high < low` / 整窗 `HHV == LLV`）
立即结束当前段；递推状态在下一段从初值重新开始，**不跨缺口伪造交叉**，`RSV` 不补 0。

### 2.2 条件（`BT_MACD_CONDITIONS_V1`）

五类 MACD 条件**分别定义、分别测试**，不共用一个含糊标签：

| 条件 | 语义 |
| --- | --- |
| `DIF_ABOVE_ZERO` | `DIF > 0` |
| `BOTH_LINES_ABOVE_ZERO` | `DIF > 0` 且 `DEA > 0` |
| `GOLDEN_CROSS` | 前一有效相邻 bar `DIF <= DEA`，当前 `DIF > DEA` |
| `DEATH_CROSS` | 前一有效相邻 bar `DIF >= DEA`，当前 `DIF < DEA` |
| `ABOVE_ZERO_GOLDEN_CROSS` | `BOTH_LINES_ABOVE_ZERO` 且 `GOLDEN_CROSS` |

KDJ 条件：`KDJ_GOLDEN_CROSS` / `KDJ_DEATH_CROSS` / `KDJ_K_ABOVE_D` / `KDJ_OVERSOLD_GOLDEN_CROSS`。

未 ready 的行一律为 `False`（不是 `True`），调用方必须显式检查 ready。

### 2.3 退出（`BT_DAILY_EXIT_V1`）

执行模式固定为 **`CLOSE_CONFIRM_NEXT_SESSION_OPEN`**：

- **收盘判断**：只使用该 session **已完成**的 RAW 日线收盘价；
- **次一 session 开盘**尝试执行（09:30，Asia/Shanghai）；
- **成本锚** = 该 lot 的**实际成交价**（含撮合滑点、不含佣金）；费用仍由原 Fee/Ledger 记账。

| 规则 | 触发条件 |
| --- | --- |
| 固定成本止损 | `收盘 <= 成交价 * (1 - stop_loss_pct)` |
| 固定止盈 | `收盘 >= 成交价 * (1 + take_profit_pct)` |
| 最高收盘价移动止损 | `peak_close >= 成交价 * (1 + activation_pct)` 后激活；`收盘 <= peak_close * (1 - trail_pct)` 触发；同一 lot 只收紧不放松；部分卖出不重置峰值 |
| 固定持有 | 按独立交易日历：`trade_session_index - entry_session_index >= fixed_holding_sessions` |

多条件同时命中时**保留全部原因**（`triggered`），主展示原因按固定优先级：
固定成本止损 → 移动止损 → 固定止盈 → 结构/指标退出 → 固定到期。该优先级不是伪造盘中先后。

同一 lot 同一时点只生成一个有效退出意图；退出意图一旦成立，不因价格反弹或新入场信号撤销。

### 2.4 正式估值（`OFFICIAL_VALUATION_V1`）

结果中的 `equity_curve` 与 `final_equity` **不来自"导出快照取最后一条"**，而是经
`engine/official_valuation.py::official_equity_curve` 抽取的正式估值点：

- **独立日历必填**：`calendar` 由调用方显式传入，不从快照反推（`calendar=None` 直接抛错）；
- 正式事件固定为 `AFTER_CLOSE`（15:30 盘后结算）；
- 应有日历内**缺任一天（含末日）** → 抛 `OfficialValuationError`，不静默跳过、不缩短端点；
- 权益必须为**有限正数**；NaN / Inf / 非数值 → 抛错；
- 同一 session 多事件按**原始插入序号**取结算终态，禁止按 equity 大小挑选；
- 每个点保留 `timestamp` / `event_kind` / `event_sequence` / `calendar_identity`。

另有一层入口校验：引擎对**缺 bar 的 session 仍会发出快照并静默结转权益**，
因此"缺整日"无法由事后估值发现。公共入口 `run_behavior_backtest_v1` 会先按声明日历
校验数据覆盖，缺任一天即 `CALENDAR_NOT_COVERED_BY_DATA` 拒绝（末日缺失会标注
`INCLUDES END SESSION`）。

无独立日历时只提供 `diagnostic_curve_without_calendar()`，其输出明确标记
`official=false` / `NON_OFFICIAL_DIAGNOSTIC`，**不得作为正式结论**。

### 2.5 外部路径约束（`BT_IO_ROOT_GUARD_V1`）

CLI 与文件入口的外部路径一律经 `resolve_within_root` 约束在调用者显式声明的根目录内：

- `dataset_path` 必须配 `dataset_root`（缺失即 `DATASET_ROOT_REQUIRED`）；
- 拒绝 `..` 父目录穿越（`PARENT_TRAVERSAL_NOT_ALLOWED`）；
- 解析符号链接后再校验归属，越界即 `PATH_OUTSIDE_IO_ROOT`；
- 结果输出同样受 `--out-root` 约束。

外部参数不得决定任意读写位置。

---

## 3. 已修复的缺陷（红 → 绿）

### 3.1 旧入口大盘过滤的开盘前视

`BacktestRunner._index_allows_buy(d)` 原用
`self.index_close.index.searchsorted(date, side="right") - 1`，在 d 日开盘调用时会取到
**d 日当天的收盘价**（未来数据），使开盘许可被当天未来行情改变。

反例（`tests/legacy_entry/test_index_filter_lookahead.py`）：保持截至 d 日开盘的全部可知输入不变，
只修改 d 日当天的指数收盘，原实现的开盘决策发生改变。修复后只使用严格早于 d 的已完成收盘，
且 d 之前无历史数据或均线预热不足时 **fail-closed**（不放行）。

修复**没有**通过关闭大盘过滤、改注释、删除测试或让所有买入失败来实现；测试包含正常成交正对照。

### 3.2 撮合容量为零时退回全量成交

`DailyBarFillModel` 原在 `int(volume * max_participation_rate) == 0` 时回退到
`order.remaining_quantity`（全量成交），与容量约束自相矛盾。现改为返回
`PARTICIPATION_LIMIT` 并拒单，不再退回全量。

### 3.3 缺整日无法被发现（正式估值接线缺陷）

原实现的 `equity_curve` / `final_equity` 只是"导出 `ledger.snapshots` 取最后一条"，
因此无法发现整日/末日缺失，也不校验 NaN/Inf。现改为经 `official_equity_curve`
抽取正式估值点，并在入口增加日历覆盖校验（见 §2.4）。

同时修复 `official_equity_curve` 自身的一个缺陷：非数值权益原会泄漏 `ValueError`，
现改为 fail-closed 的 `OfficialValuationError`。

---

## 4. 如何显式调用

### HTTP（只读计算端点）

新端点**默认被拒绝**：`ExecutionPolicy.allow_readonly_compute` 默认 `False`，
因此在默认只读策略下 `POST /api/backtest/behavior` 与其它所有 POST 一样返回
`403 EXECUTION_POLICY_READ_ONLY`。要使用它，调用方必须显式构造带该开关的策略：

```python
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.webapp import create_app

app = create_app(root, ExecutionPolicy(
    mode="READ_ONLY", workspace_kind="SYNTHETIC", allow_readonly_compute=True))
```

该开关只放行"对请求体内合成数据的纯计算"：不读研究目录、不写盘、不启动进程、
不改变任何研究状态（已有测试锁定"调用后合成 root 内容不变"）。
`governance_allowed`、`allow_structural`、`allow_process_start` 与启动禁恢复行为均未改动。

```bash
# 合同与支持范围（只读 GET，不受写策略限制）
GET /api/backtest/behavior/contracts

# 回测（请求体内合成行情；不接受文件路径，强制不写盘）
POST /api/backtest/behavior
{
  "mode": "BT_BEHAVIOR_DAILY_V1",
  "calendar": [20241001, 20241002, ...],
  "symbols": ["600000.SH"],
  "bars": {"600000.SH": [{"date": 20241001, "open": 10.0, "high": 10.1, "low": 9.9,
                          "close": 10.0, "volume": 5000000}, ...]},
  "entry_conditions": ["ABOVE_ZERO_GOLDEN_CROSS"],
  "exit_rules": {"stop_loss_pct": 0.03, "take_profit_pct": 0.10,
                 "trailing_activate_pct": 0.05, "trailing_pct": 0.08,
                 "fixed_holding_sessions": 20},
  "initial_cash": 100000.0, "max_positions": 1, "max_position_weight": 1.0,
  "commission_rate": 0.00025, "min_commission": 5.0,
  "stamp_tax_rate": 0.0005, "slippage_bps": 0.001
}
```

响应回显实际生效的 `engine_version`、`indicator_contract`、`condition_contract`、
`exit_contract`、`exit_execution_mode`、`price_mode`、`time_rules` 与 `resolved_config`。

### CLI（支持文件入口）

```bash
python scripts/run_behavior_backtest_v1.py \
    --request request.json --request-root . \
    --out result.json --out-root .
```

`request.json` 可含 `dataset_path`（CSV / Parquet，列：`symbol,date,open,high,low,close,volume[,amount]`）
与 `dataset_root`；两者必须成对给出，且 `dataset_path` 必须落在 `dataset_root` 内。
文件解析是真实的；同一请求在 CLI 与 HTTP 上得到相同的语义事件与账户结果。

### 结果字段

响应回显实际生效的 `engine_version`、`indicator_contract`、`condition_contract`、
`exit_contract`、`exit_execution_mode`、`price_mode`、`time_rules` 与 `resolved_config`；
`official_valuation` 给出正式估值的日历身份、区间与逐点事件身份。

---

## 5. 验收证据

| 证据 | 位置 |
| --- | --- |
| 合成行情（CSV 文件入口） | `reports/behavior_acceptance_v1/synthetic_bars.csv` |
| 独立预期值（手算 / 朴素递推） | `reports/behavior_acceptance_v1/independent_expectations.json` |
| 实际逐笔记录（订单/成交/lot/现金/equity） | `reports/behavior_acceptance_v1/actual_trace.json` |
| 能力矩阵 | `reports/behavior_acceptance_v1/CAPABILITY_MATRIX.json` |
| JUnit | `reports/junit-bt-behavior.xml` |
| 生成脚本 | `scripts/emit_behavior_acceptance_evidence_v1.py` |

本轮新增测试（全部走真实引擎、撮合、账本与被测指标，不做 mock）：

- `tests/indicators/test_indicator_formulas_v1.py`：MACD / KDJ / MA / EMA / CROSS 公式与因果性
- `tests/behavior/test_daily_exit_rules_v1.py`：四类退出规则
- `tests/behavior/test_full_account_chain_v1.py`：完整账户链 A–J 场景
- `tests/behavior/test_ledger_hand_calculation_v1.py`：独立手算账本核对
- `tests/behavior/test_official_valuation_wiring_v1.py`：正式估值接线（独立日历 / 缺整日 / 缺末日 / NaN-Inf）
- `tests/behavior/test_public_entrypoints_v1.py`：API / CLI 语义一致、合同暴露与路径越界拒绝
- `tests/legacy_entry/test_index_filter_lookahead.py`：旧入口前视复现与修复

---

## 6. 仍未支持的范围（明确拒绝，不静默降级）

| 请求 | 结果 |
| --- | --- |
| 盘中触价止损 / 止盈（`INTRADAY_TOUCH_STOP`） | `ExitConfigError: UNSUPPORTED_EXIT_TYPE` |
| Tick / 盘口级执行（`TICK_LEVEL_EXIT`） | `ExitConfigError: UNSUPPORTED_EXIT_TYPE` |
| 多周期执行（`MULTI_TIMEFRAME_EXIT` / `BT_BEHAVIOR_MULTI_TIMEFRAME_V1`） | `UNSUPPORTED_EXIT_TYPE` / `UNSUPPORTED_MODE` |
| 结构价止损（`STRUCTURE_STOP`） | `ExitConfigError: UNSUPPORTED_EXIT_TYPE`（RAW 成交价与 QFQ 结构价无可比证据） |
| 5MIN 撮合模式（`BT_BEHAVIOR_INTRADAY_V1`） | `UNSUPPORTED_MODE` |
| 未知条件名 / 未知请求字段 | `UNSUPPORTED_CONDITION` / `UNKNOWN_REQUEST_FIELD` |
| 声明日历未被数据覆盖（含缺末日） | `CALENDAR_NOT_COVERED_BY_DATA` |
| 文件入口缺 `dataset_root` | `DATASET_ROOT_REQUIRED` |
| 外部路径越界 / `..` 穿越 | `PATH_OUTSIDE_IO_ROOT` / `PARENT_TRAVERSAL_NOT_ALLOWED` |

CLOSE_CONFIRM 模式**不会**因为 high/low 触及阈值就猜一个成交价；请求盘中执行必须得到明确的不支持结果。

---

## 7. 可靠性结论覆盖范围

| 结论 | 状态 |
| --- | --- |
| `FORMULA_VALIDATED` | **是**（MACD_V1 / KDJ_V1 / 五类 MACD 条件 / MA-EMA-CROSS，经独立 oracle 与手算核对） |
| `ENTRYPOINT_WIRED` | **是**（HTTP + CLI 共用同一服务，参数真实透传并回显） |
| `EXECUTION_BEHAVIOR_VALIDATED` | **是**（四类日线退出 + T+1/停牌/跌停/容量/期末未成交/重复退出） |
| `ACCOUNTING_VALIDATED` | **是**（现金、lot、费用、已实现盈亏、逐日 equity 经独立预期值核对） |
| `REAL_DATA_VALIDATED` | **否**（本轮全部为合成工程合同） |
| `PROFITABILITY_VALIDATED` | **否**（且不在本轮目标内） |

软件一致性：**未做**与通达信等第三方的同输入同复权逐值对照，因此只能声明"本版本公式验收通过"，
不能声明"软件完全对齐"。

`READY_FOR_INDEPENDENT_BEHAVIOR_REVIEW=true`（本轮必要能力均有实际通过证据）。

### 保持不变的边界

- `REAL_RESEARCH_EXECUTED=false`
- `NEW_REAL_PERFORMANCE_EXPOSURES=0`
- `STRATEGY_PROFITABILITY_CERTIFIED=false`
- `MAIN_MERGED=false`
- `OLD_WORKSPACE_CHANGED=false`
- `ORIGINAL_CANDIDATE_GOAL=NOT_ACHIEVED_ARCHIVED`

---

## 8. 未决事项

**旧入口 `stop_loss_pct` 的语义分裂**：旧 `BacktestRunner` 的 `stop_loss_pct` 作用在信号结构低点上，
本轮**按兼容性要求未改**（改成成本止损会改变既有策略的全局默认行为）。因此旧入口与新入口的
"止损"含义不同。若后续要让旧入口也使用成本止损，需要一次明确的、带版本的策略语义变更决定，
不属于本轮授权范围。在此之前，两个入口的止损语义必须在文档与界面上保持区分标识。

---

## 9. 静态检查（SonarCloud）处置记录

本轮 PR 首次检查时 Quality Gate 失败，`new_security_rating = C`（要求 A）。
共 4 条 SECURITY 影响项，**全部位于本轮新增代码**，已按证据逐条处置：

| 规则 | 位置 | 判定 | 处置 |
| --- | --- | --- | --- |
| `githubactions:S8544` | `.github/workflows/backtest-behavior-acceptance.yml` | **真实缺陷**：依赖未锁定解析版本 | 改用仓库既有带哈希锁文件 `requirements-p3b.txt` + `--require-hashes`，与其它 workflow 一致 |
| `githubactions:S8541` | 同上 | **真实缺陷**：未禁用 sdist 构建脚本 | 同上，并显式加 `--only-binary ":all:" --no-binary "pytdx"` |
| `pythonsecurity:S8707` | `scripts/run_behavior_backtest_v1.py` | **真实缺陷**：CLI 外部参数可决定任意读写位置 | 新增 `resolve_within_root`，请求/结果路径必须落在显式声明的 `--request-root` / `--out-root` 内 |
| `pythonsecurity:S8707` | `src/chanlun_trader/engine/behavior_service_v1.py` | **真实缺陷**：`write_result` 可写任意路径 | 同上；并新增 `test_cli_rejects_path_outside_declared_root` 等回归 |

处置原则：**不删除检查、不扩大排除项、不绕过质量门**。4 条均为真实缺陷而非误报，因此以代码修复关闭，
未使用任何抑制标注。修复后新增的路径越界回归测试证明约束确实生效。
