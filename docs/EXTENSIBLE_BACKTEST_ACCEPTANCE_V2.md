# 可扩展回测验收 V2（EXTENSIBLE_BACKTEST_ACCEPTANCE_V2）

本文回答附件第 11 节要求的六个问题：

1. 系统现在能计算、组合、执行哪些指标和退出；
2. 哪些行为已经验证；
3. 用户通过哪个入口使用；
4. 新增指标怎样接入；
5. 哪些数据和执行模式仍不能信；
6. 每项证据是什么。

**V2 不重做 V1。** V1 的公式、初值、预热、触发时点与执行语义全部原样保留，
V2 只做增量扩展。V1 交付物见 `docs/BACKTEST_BEHAVIOR_ACCEPTANCE_V1.md`。

---

## 0. 版本身份

| 对象 | 值 |
| --- | --- |
| V1 PR | `#14`（MERGED） |
| V1 最终 HEAD | `36ccc374dd562fca33c39a198660e4c9b9daa563` |
| V1 merge SHA | `c498ee217e1f4e84eca398d690fce21beca22088` |
| V2 BASE_SHA | `c498ee217e1f4e84eca398d690fce21beca22088`（含最终 V1 的 `origin/main`） |
| V2 分支 | `codex/extensible-backtest-acceptance-v2` |
| V2 PR | Draft（base = `main`） |

`V1_MERGED=true` 不是 V2 的合并许可；两项工作分别记录。

---

## 1. V1 能力复用矩阵

| V1 能力 | 处置 | 说明 |
| --- | --- | --- |
| `MACD_V1`、`KDJ_V1`、MA/EMA/CROSS | **原样复用** | 注册表以 `REUSE_AS_IS` 别名指向 V1 实现；公式、初值、预热、输出不变 |
| 已命名 MACD 条件（5 类）、KDJ 条件（4 类） | **原样复用** | `engine/conditions_v1.py` 未改动，仍可用 |
| 成本止损 / 固定止盈 / 最高收盘移动止损 / 固定持有 | **原样复用** | `DailyExitRuleSetV2.v1_rules` 委托 V1 规则集与求值器 |
| `BacktestEngineV2` / `BrokerSimulator` / `PortfolioLedger` | **原样复用** | V2 服务不改引擎、撮合、账本 |
| 旧入口开盘前视修复 | **回归基线** | 未重复修改；`tests/legacy_entry` 原样运行 |
| 零容量拒单（`DailyBarFillModel`） | **回归基线** | 未重复修改 |
| 正式估值接线 / 独立日历 / 数据覆盖 / 路径约束 | **原样复用** | V2 服务继续走 `official_equity_curve` 与 `resolve_within_root` |
| `research/unified_factor.py` 面板级算子注册表与 DSL | **复用（未改）** | 与 V2 单证券时序层互补，不新建平行体系 |
| 盘中触价 / Tick / 盘口 / 多周期执行 | **仍不支持** | 请求即明确拒绝 |

V1 已关闭的问题**不再重复调查**；只有当前代码下能实际复现的新缺陷才最小修复（见 §6）。

---

## 2. 能力总览

### 2.1 指标（`BT_INDICATOR_REGISTRY_V2`，51 项 / 7 家族）

| 家族 | 指标 |
| --- | --- |
| 均线与趋势 | `MACD`(V1)、`MA`、`SMA_TDX`、`EMA`、`WMA`、`RMA`、`DEMA`、`TEMA`、`MACD_HIST_RAW`、`DMI`、`SAR` |
| 动量与震荡 | `KDJ`(V1)、`RSI`、`CCI`、`WILLIAMS_R`、`ROC`、`MTM`、`BIAS`、`TRIX`、`PSY` |
| 波动与通道 | `TRUE_RANGE`、`ATR`、`NATR`、`ROLLING_VOLATILITY`、`BOLLINGER`、`DONCHIAN`、`KELTNER` |
| 量价与资金代理 | `VOLUME_MA`、`AMOUNT_MA`、`RVOL_INCL_CURRENT`、`RVOL_PRIOR`、`OBV`、`MFI`、`ACCUMULATION_DISTRIBUTION`、`CHAIKIN_MONEY_FLOW`、`PVT`、`VWAP_SESSION_PROXY`、`HLC3`、`ROLLING_VWAP`、`TURNOVER_RATE` |
| 价格结构 | `PRICE_EXTREMES`、`PRIOR_BREAKOUT`、`DRAWDOWN_FROM_PEAK`、`BAR_SHAPE`、`STREAK` |
| 统计与截面组合 | `HISTORICAL_RETURN`、`ROLLING_SLOPE`、`TIME_SERIES_ZSCORE` |
| 自定义组合（fixture） | `VOLUME_BREAKOUT_SCORE`、`TREND_STRENGTH_RATIO`、`RSI_REGIME_FLAG` |

每个指标都带机器可读契约：`indicator_id / version / family / outputs / params /
inputs / price_mode / frequency / available_at_rule / warmup_bars / lookback /
missing_policy / unit / requires_extra_data / evidence`。
完整快照见 `reports/v2_acceptance/INDICATOR_REGISTRY_V2.json`。

**容易混淆的口径已分别命名、分别版本化**：

| 易混对 | 区别 |
| --- | --- |
| `MA` vs `SMA_TDX` | 算术均值 vs 通达信递推 `Y=(M*X+(N-M)*Y')/N` |
| `EMA` vs `RMA` | `alpha=2/(N+1)` vs `alpha=1/N`（Wilder） |
| `MACD` vs `MACD_HIST_RAW` | `HIST=2*(DIF-DEA)` vs `HIST_RAW=DIF-DEA` |
| `RVOL_INCL_CURRENT` vs `RVOL_PRIOR` | 分母是否含当前 bar |
| `VWAP_SESSION_PROXY` vs `HLC3` vs `ROLLING_VWAP` | 成交均价代理 vs 典型价代理 vs 滚动 VWAP，三者不同名、禁止互相顶替 |
| `TIME_SERIES_ZSCORE` vs 截面 `rank/percentile` | 时序标准化 vs 同 bar 截面排名 |
| `WILLIAMS_R` | 使用 0~100 表达，不返回负值形式 |
| `BOLLINGER` | `ddof` 默认 0（总体），样本口径需显式传 1 |

### 2.2 条件与表达式（`BT_CONDITION_LAYER_V2`）

支持：比较（`gt/ge/lt/le/eq/ne`）、逻辑（`and/or/not`）、交叉（`cross_up/cross_down`）、
位置（`above/below`）、区间（`between`）、滚动逻辑（`every`/`exist`/`barslast`）、
时序（`shift/ref/delta/pct_change`）、截面（`rank/percentile/top_n`）、算术（`add/sub/mul/div`）。

**三值逻辑**是本层核心安全约定：

| 表达式 | 结果 |
| --- | --- |
| `NOT(UNKNOWN)` | **`UNKNOWN`**（绝不变成买入资格） |
| `AND(FALSE, UNKNOWN)` | `FALSE` |
| `AND(TRUE, UNKNOWN)` | `UNKNOWN` |
| `OR(TRUE, UNKNOWN)` | `TRUE` |
| `OR(FALSE, UNKNOWN)` | `UNKNOWN` |

只有 `TRUE` 进入信号；`FALSE` 与 `UNKNOWN` 都被拒绝，且原因分别标记为
`CONDITION_FALSE` / `CONDITION_UNKNOWN`。

安全边界：受限表达式树，**不使用** `eval`/`exec`/动态导入/文件/网络访问；
深度上限 16、节点上限 256；`shift/ref` 负周期与居中窗口拒绝；
`CROSS` 要求前后两个合格且相邻的观察点，不跨缺口捏造交叉。

### 2.3 退出规则（`BT_DAILY_EXIT_V2`）

执行模式固定 `CLOSE_CONFIRM_NEXT_SESSION_OPEN`：**已完成收盘判断 → 下一 session 开盘尝试执行**。

| 规则 | 锚 / 触发 |
| --- | --- |
| `FIXED_COST_STOP`（V1 复用） | 实际成交价 `*(1-stop_pct)`；收盘 <= 线 |
| `FIXED_TAKE_PROFIT`（V1 复用） | 实际成交价 `*(1+profit_pct)`；收盘 >= 线 |
| `TRAILING_CLOSE_STOP`（V1 复用） | 已完成收盘峰值；只收紧；部分卖出不重置 |
| `FIXED_HOLD`（V1 复用） | 独立日历 `entry_session_index + N` |
| `ATR_DISTANCE_STOP`（V2 新增） | `成交价 - multiple * 入场前已可用 ATR`；与执行价同尺度 |
| `ATR_TRAILING_STOP`（V2 新增） | 峰值 - multiple * ATR；`tighten_only` 默认只收紧 |
| `INDICATOR_CONDITION_EXIT`（V2 新增） | 表达式为 **TRUE** 才退出 |
| `REVERSE_SIGNAL_EXIT`（V2 新增） | 表达式为 **TRUE** 才退出 |
| `STRUCTURE_PRICE_STOP`（V2 新增） | 显式声明 RAW 尺度；QFQ 无转换证据时**拒绝配置** |

仍不支持：`INTRADAY_TOUCH_STOP`、`TICK_LEVEL_EXIT`、`MULTI_TIMEFRAME_EXIT`。

每种规则都明确了：用什么价格/指标作锚、何时可知、何时触发、何时生成订单、
何时最早成交、无法成交时如何等待。多原因命中时保留全部原因，
主展示原因按固定优先级（不代表盘中先后）。

---

## 3. 用户通过哪个入口使用

### HTTP

```bash
# 合同与注册表（只读 GET）：界面据此渲染指标，无需硬编码
GET /api/backtest/behavior/contracts

# V2 通用回测（注册表驱动 + 表达式条件）
POST /api/backtest/behavior/v2
{
  "mode": "BT_BEHAVIOR_DAILY_V2",
  "calendar": [20240902, ...],
  "symbols": ["600000.SH"],
  "bars": {"600000.SH": [{"date": 20240902, "open": 10.0, "high": 10.2, "low": 9.8,
                          "close": 10.1, "volume": 5000000}, ...]},
  "indicators": [{"indicator_id": "RSI", "params": {"window": 14}},
                 {"indicator_id": "EMA", "params": {"window": 20}}],
  "entry_condition": {
    "op": "and",
    "args": [
      {"op": "gt", "args": [{"op": "indicator", "args": ["RSI"], "params": {"output": "rsi"}},
                            {"op": "const", "params": {"value": 40}}]},
      {"op": "gt", "args": [{"op": "field", "args": ["close"]},
                            {"op": "indicator", "args": ["EMA"], "params": {"output": "ema"}}]}
    ]
  },
  "exit_rules": {"stop_loss_pct": 0.05, "fixed_holding_sessions": 10},
  "initial_cash": 100000.0, "max_positions": 1, "max_position_weight": 1.0
}
```

响应回显实际生效的 `registry_version`、`indicators_version`、`condition_contract`、
`exit_contract`、`exit_execution_mode`、`price_mode`、`time_rules` 与 `resolved_config`
（含 `condition_trace`：逐证券的 TRUE/FALSE/UNKNOWN 计数）。

### CLI

```bash
# 列出注册表中的全部指标、输出、参数与自定义条件
python scripts/run_behavior_backtest_v2.py --list-indicators

# 回测（支持 CSV/Parquet 文件入口）
python scripts/run_behavior_backtest_v2.py \
    --request request.json --request-root . \
    --out result.json --out-root .
```

### Web

前端 `通用回测 V2` 标签页（`frontend/src/components/GenericBacktestPanel.vue`）：
指标下拉与参数表**由注册表动态填充**（不硬编码 MACD/KDJ），
可编辑条件表达式或选用自定义组合 fixture，提交到同一服务。

### 边界（三个入口一致）

- `POST /api/backtest/behavior/v2` 默认**被拒绝**：需显式
  `ExecutionPolicy(allow_readonly_compute=True)`；默认只读策略下返回
  `403 EXECUTION_POLICY_READ_ONLY`；
- HTTP 入口只接受请求体内合成行情（不接受 `dataset_path`），强制不写盘；
- 路径受显式根约束（`PATH_OUTSIDE_IO_ROOT` / `PARENT_TRAVERSAL_NOT_ALLOWED`）；
- 经典 `/api/backtest` 仍为 `LEGACY_EXECUTION_DISABLED`，未静默切换。

---

## 4. 新增指标怎样接入

新增一个指标**不需要**修改 Engine / Broker / Ledger 的指标名白名单。三步：

**第 1 步：实现计算函数**（放在指标层，签名 `(PriceInput, **params) -> IndicatorFrameV2`）

```python
from chanlun_trader.engine.indicators_v2 import IndicatorFrameV2, PriceInput

def my_indicator(data: PriceInput, *, window: int = 20) -> IndicatorFrameV2:
    # 复用 V1/V2 的分段与滚动工具，保持跨缺口语义一致
    ...
    return IndicatorFrameV2(
        indicator_id="MY_INDICATOR", version="MY_INDICATOR_V1",
        index=data.index, columns={"value": pd.Series(values, index=data.index)},
        ready=pd.Series(ready, index=data.index),
        segment=pd.Series(segments, index=data.index),
        warmup_bars=window,
    )
```

**第 2 步：登记契约**

```python
from chanlun_trader.engine.indicator_registry_v2 import _adapter, _spec, NEW_IN_V2

registry.register(
    _spec(
        "MY_INDICATOR", "MY_INDICATOR_V1", "自定义组合", "我的指标", NEW_IN_V2,
        ["value"], {"window": 20}, ("close",),
        warmup_bars=20, lookback=20, unit="RATIO",
        formula_note="明确写出公式与初值口径",
        implementation_path="src/chanlun_trader/engine/my_indicators_v2.py",
        evidence={"IMPLEMENTED": True, "REGISTERED": True},
    ),
    _adapter(my_indicator, ["value"]),
)
```

**第 3 步：补测试**（独立 oracle + 契约测试）。未附必要契约、独立预期值和测试时，
不可标记为 `FORMULA_VALIDATED`；实现 hash 变化时旧证明失效。

接入后立刻可通过 `POST /api/backtest/behavior/v2`、CLI 与 Web 使用，
并可被条件表达式引用（`{"op": "indicator", "args": ["MY_INDICATOR"], "params": {"output": "value"}}`）。

**本轮已用三个 fixture 证明该机制**：`VOLUME_BREAKOUT_SCORE`（多输入）、
`TREND_STRENGTH_RATIO`（多输入）、`RSI_REGIME_FLAG`（依赖另一个指标），
均未修改引擎核心即进入账户链。

---

## 5. 验证状态（按维度分别记录）

状态绑定具体周期、价格模式、参数域与来源。本轮全部为**合成工程证据**。

### 5.1 覆盖矩阵口径（证据适用性修正后）

**逐维度分别判定**，全部声明维度为真才记 `VERIFIED`：

| 维度 | 判定依据 |
| --- | --- |
| `FORMULA_VALIDATED` | 有独立数值 oracle，且该测试**确实调用**了此指标的实现函数 |
| `ENTRYPOINT_VALIDATED` | 有公开入口测试，且该测试**确实消费**了此 `indicator_id` |

关键约束：**证据存在不等于覆盖该项能力**。入口测试若只消费 `RSI`/`EMA`，
就不能给其它指标作入口证明；条件层各维度必须用**覆盖该维度正向语义**的测试，
不能用同一个 Top-N 测试给所有维度作证。每项证据记录适用域
（输出名/参数/价格模式/预热）、断言内容与同 HEAD JUnit。

| 家族 | VERIFIED | PARTIAL | 合计 |
| --- | --- | --- | --- |
| 基础算子 | 4 | 6 | 10 |
| 均线与趋势 | 2 | 8 | 10 |
| 动量与震荡 | 1 | 7 | 8 |
| 波动与通道 | 1 | 5 | 6 |
| 量价与资金代理 | 0 | 8 | 8 |
| 价格结构 | 0 | 5 | 5 |
| 统计与截面组合 | 2 | 5 | 7 |
| **合计** | **10** | **44** | **54** |

**10 项 VERIFIED = 4 个指标 + 6 个条件函数维度**，含义不同，不可混称"10 项完整认证"：

| 类别 | 项 | 已验证的是 |
| --- | --- | --- |
| 指标（公式 + 入口） | `MA`、`EMA`、`RSI`、`ATR` | 公式逐值正确，且经公开入口真实消费 |
| 条件函数维度 | `boolean`、`EVERY/EXIST`、`BARSLAST`、`CROSS_UP/CROSS_DOWN`、`截面rank/percentile`、`Top-N` | 该算子正向语义正确 |

这**不是** 10 项完整账户链认证。`FORMULA_VALIDATED` 只表示公式经独立数值 oracle 验证；
`ENTRYPOINT_VALIDATED` 只表示有公开入口测试真实消费了该指标；
`ACCOUNTING_VALIDATED` 是**独立维度**，仅 `ATR`（经 ATR 规则账户链）等少数项间接覆盖，
不随本表自动成立。

条件维度的子维度分别记录：`CROSS_UP` 与 `CROSS_DOWN` 各有正向测试；
`rank` 目前只有"需要截面视图"的拒绝测试，因此标 `PARTIAL`，仅 `percentile` 为 `VALIDATED`。

`PARTIAL` 的原因**不是统一的"缺 oracle"**，逐项写明：

| 原因 | 典型项 |
| --- | --- |
| 有独立 oracle，但无公开入口测试消费它 | `WMA`、`RMA`、`SMA_TDX`、`MACD`、`KDJ`、`DMI`、`SAR`、`WILLIAMS_R`、`ROC`、`MTM`、`BIAS`、`BOLLINGER`、`OBV`、`BAR_SHAPE`、`STREAK`、`ROLLING_SLOPE`、`TIME_SERIES_ZSCORE` |
| 只有负向测试，无正向正确性测试 | `REF`（仅 `test_negative_lag_rejected`）、`arithmetic`（仅算子存在性/拒绝路径） |
| 未接线到 V2 单证券公开链路 | `OPERATOR_LAYER:*`（既有面板级算子层） |
| 尚无独立数值 oracle | 其余未列入 oracle 映射的指标 |

`MACD`/`KDJ` 的 oracle 位于 **V1 兼容回归**套件
（`tests/indicators/test_indicator_formulas_v1.py`，参数化节点），
因此其证据关联 `reports/junit-v1-compat.xml`，而非 V2 新增套件。

矩阵中声称的每个 nodeid 都由测试**实际收集**验证存在；曾有 2 个 nodeid
（MACD/KDJ）写错测试函数名，已由该检查发现并修正。

**范围声明**：受审映射是**人工登记**的显式对应关系，只能保证"已登记的映射"
指向真实覆盖该能力的测试；它**不能**自动识别任意无关测试的映射。
因此不宣称无限范围的自动认证。每条证据记录自己的 suite 与 JUnit 文件，
不用单一字段指代不同来源（`MACD`/`KDJ` 的 oracle 在 V1 兼容套件，
共享的注册表契约测试在 V2 套件）。

### 5.2 本轮仍未关闭的基础能力（保留 OPEN）

以下项**确实缺失**，本轮不以补齐它们为前置，如实保留为 OPEN：

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 44 项 PARTIAL 的独立数值 oracle / 入口覆盖 | OPEN | 逐项原因见 §5.1：缺 oracle、缺入口消费、或仅有负向测试 |
| 既有面板级算子层接入 V2 单证券公开链路 | OPEN | `research/unified_factor.py` 本轮未改，也未接线 |
| 截面与时序算子的混合表达式 | 不支持 | 运行前显式拒绝，矩阵已标出 |
| 多周期精确执行 | 不支持 | 请求即拒绝 |
| 盘中触价 / Tick / 盘口 | 不支持 | 请求即拒绝 |
| `TURNOVER_RATE` 历史流通股本 | 数据依赖未满足 | 明确拒绝，不生成替代值 |

**总目标未全部完成**：本轮关闭的是"已实现部分的实际消费端正确性"与"证据不过度声明"，
不是全部 51 项指标的公式认证。

### 5.3 维度状态

| 维度 | 状态 |
| --- | --- |
| `IMPLEMENTED` | 51 项指标 + 表达式层 + 9 类退出 |
| `FORMULA_VALIDATED` | 矩阵中 10 项 VERIFIED（含独立 oracle 与入口证据）；其余为 PARTIAL |
| `CAUSALITY_VALIDATED` | 逐前缀一致 + 追加未来不改过去（多家族抽样） |
| `REGISTERED` | 51 项（契约完整，含输出名/参数/预热/缺失政策/公式指纹） |
| `ENTRYPOINT_WIRED` | API + CLI + Web 三入口同一服务 |
| `EXECUTION_BEHAVIOR_VALIDATED` | 9 类退出实际触发并经过委托/成交/费用/现金/持仓 |
| `ACCOUNTING_VALIDATED` | 现金/lot/费用/已实现盈亏/逐日权益经独立手算核对 |
| `REAL_DATA_VALIDATED` | **否** |
| `PROFITABILITY_VALIDATED` | **否**（且不在本轮目标内） |

**192 项 V2 测试是真实证据，但不等于 51 项指标的所有行为均已验证**；
未列入 VERIFIED 的项按 PARTIAL 如实披露。分母按来源分开：
V2 主体 121、PR15 定向 71（第一轮 30 + 残留与指纹 27 + 依赖执行 14）。

软件对齐：未做与通达信/TA-Lib 的同输入同复权逐值对照，故只声明
"本版本公式验收通过"，不声明"与第三方软件完全一致"。

---

## 6. 本轮修复的缺陷

### 6.1 定向复核（PR #15，reviewed HEAD `196e666`）关闭的五类根因

| 编号 | 根因 | 处置 |
| --- | --- | --- |
| **PR15-01** | 同一请求里 `MA(5)` 与 `MA(20)` 共用 `results['MA']`，后者静默覆盖前者；表达式 `version` 未核验 | 引入 `IndicatorInstanceKey`（id/version/参数/输入/价格域/周期）与 `alias` 引用；重复与冲突身份**先拒绝**（`DUPLICATE_INDICATOR_INSTANCE` / `CONFLICTING_INDICATOR_INSTANCE`）；显式 `version` 精确解析，未知版本报错 |
| **PR15-02** | ATR 入场锚回退到**入场当日** ATR（前视）；距离与跟踪共用一条未核验 ATR；`DYNAMIC_CURRENT` 接受参数后被忽略 | 新增 `AtrBinding`（窗口/版本/价格尺度/可用时间）；`freeze_entry_anchor` 取**严格早于入场日**的最近可用 ATR，在持仓创建时由 `fill_hook` 冻结；规则依赖必须显式声明（`ATR_DEPENDENCY_NOT_DECLARED`）；无法执行时**拒绝运行**（`EXIT_RULE_NOT_EXECUTABLE`）而非报成正常完成 |
| **PR15-03** | 多证券共用一个条件上下文，或直接拒绝多证券 | 上下文改为**按 `lot.symbol`** 取；缺失某证券即拒绝（`CONDITION_CONTEXT_MISSING_FOR_SYMBOL`）；仅基础退出时**不构建**指标上下文 |
| **PR15-04** | Top-N 在全 NaN 时"选中 A"、用 NaN 凑满、Inf 参与排名、UNKNOWN 填 FALSE 后在 NOT 分支变成可买 | 先构建每 session 合法截面（成员资格 + ready + finite + 时间可见性）再排序；不合格成员保持 **UNKNOWN**；Inf 被排除；截面与时序操作数索引不一致时明确报错（`CONDITION_OPERAND_INDEX_MISMATCH`） |
| **PR15-05** | `build_matrix` 按字符串前缀判定 `met=True`；实现 hash 只哈希适配器包装器 | 逐项按维度判定证据适用性（见 §5.1）；指纹**递归**绑定依赖 version 与源码 |

### 6.2 本轮在修复过程中发现并关闭的额外真实缺陷

| 缺陷 | 性质 | 处置 |
| --- | --- | --- |
| `DMI/ADX` 的 ADX 放大约 N 倍（漏除 window） | **真实公式错误** | `_wilder_smooth` 返回累加和，ADX 是平均值；补除 `window`，并与独立 oracle 逐值对齐 |
| `_wilder_smooth` 种子被段首 NaN 污染 | **真实缺陷** | 种子改为取前 N 个**有限**值之和 |
| `DMI` 段首 TR 因缺前收盘而丢弃 | 口径不一致（预热偏移） | 段首按惯例取 `H-L`，与 `true_range` 一致 |
| `DYNAMIC_CURRENT` 在首根被跳过（提前 return） | **真实缺陷**（参数被忽略） | 首次创建状态后继续走更新逻辑；取不到当日 ATR 时按合同拒绝 |

### 6.7 依赖作用域防误用（reviewed HEAD `c6af41a`）

`DependencyScope` 只约束**使用 `compute_dependency` 的实现**。若某个指标实现内部
直接调用 `registry.compute("DEP")`，仍会静默走默认最新版并绕过固定版本
（复现：pinned `DEP_V1`，父输出却为 `DEP_V2` 的值）。

**处置**：依赖求值期间，若该指标在作用域里已被固定版本，直接 `compute()`
（未显式传 `version`）会被拒绝：

```
DIRECT_COMPUTE_BYPASSES_PINNED_DEPENDENCY:<id>:pinned=<version>:
请在指标实现内使用 compute_dependency()，否则会静默使用默认最新版而非固定版本
```

三条边界：

- 依赖**未被固定**时，直接 `compute()` 不构成绕过（不误报）；
- 显式传 `version` 时不算绕过（调用方已明确指定版本）；
- `compute_dependency` 内部走带标记的调用路径，不会被自身防护拦下。

**同时修正了仓库内真实的绕过点**：`RsiRegimeProvider` 原先直接
`registry.compute("RSI", ...)`，现已改为 `compute_dependency`，
并为 `RSI_REGIME_FLAG` 登记 `pinned_versions={"RSI": "RSI_V1"}`。

**风险降级说明**：这是**运行时防护**，不是类型系统级的架构强制。
它把"第三方自定义指标绕过固定版本"从静默错误变为显式失败，
但实现者仍须使用 `compute_dependency` 才能获得版本锁定。

### 6.6 依赖执行与指纹一致（reviewed HEAD `c9a63ed`）

| 编号 | 问题 | 处置 |
| --- | --- | --- |
| **B1** | `pinned_versions` 只影响指纹，**不约束计算**：父公式内部 `compute('DEP')` 仍取默认最新版，父输出随依赖升级漂移（复现：1 → 2 → 99），而父 hash 不变 | 新增 `DependencyScope` 与 `registry.compute_dependency`：父实现取依赖时走**同一解析结果**，固定版本真实约束计算；作用域按调用栈嵌套，父与嵌套固定版本不被其他节点默认值覆盖；缺版本/缺实现/作用域外调用明确拒绝 |
| **B2** | 判环用单一 `seen` 集合，把**合法共享依赖**（PARENT→A、PARENT→B、B→A）误判为 `CIRCULAR_DEPENDENCY:A` | 判环只针对**当前递归路径**（`path`），已完成节点进 `done` 缓存；共享依赖通过，真自循环与回边仍拒绝 |

**B1 的验证方式**：父公式**真实消费**依赖（调用 `compute_dependency` 并把值写入自身输出），
不用常数或手工伪造 trace。固定 `DEP_V1` 后加入/修改 `DEP_V2`（值 2 → 99），
父输出恒为 1.0、父 hash 恒等；未固定时默认解析同时决定父输出与父 hash。

**B2 的验证方式**：合法共享 DAG 通过且指纹确定；自循环与回边（A→B→A）仍抛
`CIRCULAR_DEPENDENCY`；注册顺序不改变结果。

### 6.5 证据适用性与依赖版本指纹（reviewed HEAD `204ccaa`）

| 编号 | 问题 | 处置 |
| --- | --- | --- |
| **A** | 证据**存在**不等于覆盖该项能力：入口 nodeid 统一用只消费 RSI/EMA 的测试给所有指标作证；条件层各维度统一用 Top-N 测试作证 | 建立受审映射：指标 → 真实调用其实现函数的 oracle 测试；指标 → 真实消费其 `indicator_id` 的入口测试；条件维度 → 覆盖该维度正向语义的测试。逐维度分别判定，全部为真才 `VERIFIED`。MACD/KDJ 的 oracle 正确关联到 **V1 兼容** JUnit。每项记录适用域、断言内容与同源树 JUnit |
| **B** | `_formula_hash` 从 `registry.specs()` 取**第一个**版本，而 `get(id)` 取**最大**版本 → 执行选 V2 却哈希 V1 | 指纹与执行共享同一解析（`_resolve_dependency`）：绑定 id、version、实现内容与递归依赖；支持显式固定版本且不被默认最新版替换；缺依赖/歧义/循环明确拒绝 |

**A 的量化后果**：`VERIFIED` 从 28 项**降到 10 项**。此前把所有 51 个指标名都写进
`entrypoint_verified` 集合，等于用 RSI/EMA 的入口测试给 47 个指标作证。
现在只有 4 个指标同时具备公式与入口证据（`MA`/`EMA`/`RSI`/`ATR`），
加条件层 6 个维度。

**B 的复现与验证**：仅 `DEP_V1` 时父公式指纹为 X；新增 `DEP_V2` 后默认执行切到
V2，指纹必须随之改变。修复前指纹不变（选 V2 哈希 V1），修复后正确改变。
配套测试覆盖：固定版本不被替换、改**未选中**版本不改变指纹、
改**选中**版本改变指纹、缺依赖拒绝、循环依赖拒绝、注册顺序正对照。

### 6.4 残留修正（reviewed HEAD `ae871ae`）

第一轮定向修正后仍有四处残留会改变**实际条件与止损含义**，本轮集中关闭：

| 编号 | 残留 | 处置 |
| --- | --- | --- |
| **PR15-01** | 未知 `version` 只校验 `indicators[].version`；写在 `entry_condition`/`exit_condition`/`reverse_signal_condition` 内的版本被静默忽略 | 表达式引用解析为**精确实例键**并核验版本：未知引用 `UNKNOWN_INDICATOR_REFERENCE`，版本不符 `VERSION_MISMATCH`，均在运行前拒绝；多实例时裸 id 引用同样拒绝，单实例保持兼容 |
| **PR15-02** | `series[symbol]` 被最后一条规则覆盖；`_entry_atr[lot_id]` 无法区分规则/窗口；窗口从 `spec.params` 默认值重推，无 alias 的 `ATR(7)` 被误配到 14 | 每条规则各自持有序列与绑定身份（`rule -> {symbol: series}`）；锚按 `(lot_id, rule)` 存储；窗口取自 `IndicatorResult.resolved_params`（**本次实际使用值**），并随 trace 输出绑定身份 |
| **PR15-04** | 排名结果为 symbol 索引，退出端一律按 session 查，导致排名退出**永不触发** | 按结果索引域取值：截面结果按 `lot.symbol`，时序结果按 `trade_session`；混合表达式在运行前显式拒绝并在矩阵标出 |
| **PR15-05** | 矩阵证据只写目录名；公式指纹只拼接依赖**名称** | 逐项给出精确 nodeid + 适用域 + 同 HEAD JUnit，且测试会**实际收集**每个 nodeid（不存在即失败）；指纹**递归**绑定依赖的 version 与源码，附"只改依赖实现"的变异测试 |

### 6.3 更早（V2 主体）已修复的缺陷

| 缺陷 | 性质 | 处置 |
| --- | --- | --- |
| 条件退出取"上下文最后一根"而非当前 `trade_session` | **真实缺陷**（错误 + 前视风险） | 改为按 `trade_session` 取值 |
| `DailyExitEvaluatorV2` 同一 lot/session 产生两条评估记录 | **真实缺陷**（重复退出误判） | V1 委托记录不再重复并入 |
| 调用方指定的指标 `version` 被静默忽略 | **真实缺陷**（契约不生效） | 按 version 精确解析 |
| `TR` 首根因缺前收盘而丢弃 | 口径不一致 | 段首取 `H-L` |
| 截面 `top_n` 用名为 `symbol` 的列与同名索引冲突 | **真实缺陷**（运行时异常） | 改用位置数组 |
| `_series` 返回 RangeIndex 导致按日期 reindex 全 NaN | **真实缺陷**（指标全 UNKNOWN） | 显式把交易日整数键设为索引 |
| RSI 边界用浮点相等判断零涨跌 | 稳健性缺陷 | 改为容差比较（`NaN == NaN` 为 False，`isfinite` 另行排除 Inf） |
| CLI 结果写入未走服务层根约束 | **安全缺陷**（Sonar `S2083`） | 写入移入服务层 `write_result_v2` + 类型化结果对象 |

以上均为 V2 新增代码中的缺陷，**不是** V1 收尾遗留问题。

---

## 7. 仍不能信的范围（诚实披露）

### 7.1 数据依赖未满足

| 能力 | 依赖 | 本轮状态 |
| --- | --- | --- |
| `TURNOVER_RATE`（换手率） | 历史流通股本 | 缺字段返回 `DATA_DEPENDENCY_NOT_MET`；不生成随机值/0/当前快照替代 |
| 真实 VWAP | 同口径金额/股数或逐笔数据 | 只提供三个**不同名代理**；`REAL_DATA_VALIDATED=false` |
| 行业强弱 | 时点行业成员 | 未实现 |
| 盘口 / 大单 / 筹码 / 龙虎榜 / 基本面 | 对应历史源与可用时间 | 未实现 |

所谓"资金流"（`MFI`/`CMF`/`A/D`/`PVT`）是**公式代理**，不是实际主力净买卖。

### 7.2 执行模式

- 盘中触价、Tick/盘口、集合竞价：**明确拒绝**；
- 日线高低价**不能**证明盘中触及先后；跳空后不保证在止损线成交；
- 多周期精确执行未验收；已完成周线引用属于本轮计算层能力，
  不等于多周期成交模型已认证。

### 7.3 未实现的扩展项（如实登记，不谎报）

`KAMA`、`DMA`、`DPO`、`VR`、`BR/AR`、`CR`、`EMV`、`Supertrend` 未实现；
蜡烛形态仅 `BAR_SHAPE` 比例；分型/笔/中枢/背驰属既有缠论引擎，未经 V2 认证。

### 7.4 既有 OPEN（本轮不自动关闭）

公司行动、真实输入、分红税费、幸存者偏差、历史访问事件等 OPEN 项继续披露。
原候选保持 `NOT_ACHIEVED_ARCHIVED`。

### 7.5 既有环境失败

与 BASE_SHA 逐条一致，**非本轮引入**，不计为通过：
本地未设合成隔离变量的治理门禁（`NOVELTY_IMPORT_ISOLATION_REQUIRED`、
`OBJECTIVE_V2_SYNTHETIC_GOVERNANCE_REQUIRED`、`BATCH_SYNTHETIC_GOVERNANCE_REQUIRED`）、
缺本机研究工件（`FileNotFoundError: data/research/event_store/*.parquet`）、
前端未构建（6 项 404）、需封存期真实产物的完整性测试。
逐条清单见 `reports/behavior_acceptance_v1/ENVIRONMENT_FAILURES.json`。

---

## 8. 交付物

| 交付物 | 位置 |
| --- | --- |
| 验收范围（冻结） | `ACCEPTANCE_SCOPE.json` |
| 能力盘点 | `reports/v2_acceptance/CAPABILITY_INVENTORY.json` |
| 覆盖矩阵 | `reports/v2_acceptance/CAPABILITY_MATRIX_V2.json` |
| 指标契约快照 | `reports/v2_acceptance/INDICATOR_REGISTRY_V2.json` |
| 范围生成脚本 | `scripts/emit_v2_acceptance_scope_v1.py` |
| V2 CLI | `scripts/run_behavior_backtest_v2.py` |
| 双平台 CI | `.github/workflows/extensible-backtest-acceptance-v2.yml` |

### 8.1 测试分母（按证据来源分开统计，不合并成一个数字）

| 来源 | 目录 | 项数 | 说明 |
| --- | --- | --- | --- |
| 指标公式/因果/边界 | `tests/indicators_v2` | 58 | 独立数值 oracle + 手算 |
| 条件层三值逻辑与安全 | `tests/conditions_v2` | 26 | 真值表、CROSS 双向、安全拒绝 |
| 退出规则与账户链 | `tests/exits_v2` | 28 | 9 类退出、完整账户正反例 |
| 公共入口一致性 | `tests/entrypoints_v2` | 9 | API/CLI 同语义、边界拒绝 |
| **V2 主体小计** | | **121** | |
| PR15 定向（第一轮 + 证据负向检查） | `tests/pr15_remediation` | 30 | 五类根因与证据适用性负向检查 |
| PR15 定向（残留 + 指纹） | `tests/pr15_residual` | 27 | 版本绑定、ATR 规则身份、排名退出 |
| PR15 定向（依赖执行一致性） | `tests/pr15_dependency` | 14 | 固定版本约束计算、共享依赖 DAG、绕过防护 |
| **PR15 定向小计** | | **71** | |
| **合计** | | **192** | |

V1 兼容回归单独统计：`tests/behavior`、`tests/indicators`、`tests/legacy_entry`、
`tests/engine`、`tests/golden`、`tests/lookahead`、`tests/regression` 共 **139** 项。

证据类型分开记录，不混为"192 项都验证了公式"：

- **helper 级**：直接调用注册表/求值器（如公式逐值 oracle）；
- **直接服务级**：调用 `run_behavior_backtest_v2`；
- **HTTP 级**：经 FastAPI `TestClient` 的 `POST /api/backtest/behavior/v2`；
- **CLI 级**：真实子进程执行 `scripts/run_behavior_backtest_v2.py`。

矩阵中的 `VERIFIED` 同时要求 oracle 与**公开入口**证据，且给出精确 nodeid。

### 8.2 回归对账口径

BASE_SHA 干净工作树与本次 HEAD 各跑完整套件，逐条对账失败 nodeid：

- 三处历史记录数字（文档 194/193、归档 194/194、早期 170/170）来自不同阶段与不同
  隔离设置，**不能互相替代**；本文件只声明本次 HEAD 的对账结果。
- 差异项为既有顺序相关不稳定（Windows GBK 解码/共享状态），两个 flaky 用例
  单独运行均通过，且 V2 模块未被 `research_factory` 引用。
- **因果未确认**：无法证明该不稳定的触发条件，因此只声明"本次 HEAD 未出现新增
  失败 nodeid"，**不**声明"全系统零回归"。
- 既有环境失败与 BASE_SHA 逐条一致，按 nodeid 对账，不计为通过。

---

## 9. 边界保持

```text
V1_PR_NUMBER = 14
V1_MERGED = true
V1_FINAL_HEAD = 36ccc374dd562fca33c39a198660e4c9b9daa563
V1_MERGE_SHA = c498ee217e1f4e84eca398d690fce21beca22088
V2_BASE_SHA = c498ee217e1f4e84eca398d690fce21beca22088
V2_MAIN_MERGED = false
STRATEGY_PROFITABILITY_CERTIFIED = false
REAL_RESEARCH_EXECUTED = false
NEW_REAL_PERFORMANCE_EXPOSURES = 0
OLD_WORKSPACE_CHANGED = false
```

未读取真实行情或封存期，未重跑 D，未搜索盈利策略，未新增真实绩效试验，
未修改预算/Trial/真实研究授权，未运行 Paper/实盘，未部署、未 merge、未 force push。
