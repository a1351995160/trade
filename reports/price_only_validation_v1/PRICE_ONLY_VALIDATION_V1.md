# 纯指标验收 V1：现有 OHLC(VA) 指标的公式、条件消费与合成入口

**任务**：按五份盘点材料推进"现有 OHLC(VA) 指标的公式、参数、条件消费验收"，不开展盈利搜索。

**顺序**：①固定包含 V1/V2 的源码基线 → ②更正盘点矛盾并隔离 gbbq 调用路径 → ③A0 合成验收 → ④A1 真实 RAW 小样本。

---

## 0. 身份

| 项 | 值 |
| --- | --- |
| 正式仓库 | `a1351995160/trade` |
| **BASE_SHA** | `f5acb0317719a42eb7071504048e341b50a7ac12`（含 PR #14 V1 + PR #15 V2） |
| 工作区 | `E:/llmwiki/trade-price-only-validation-v1`（新建干净 worktree） |
| 分支 | `codex/price-only-indicator-validation-v1` |
| Python | 3.13.5（本地）/ 3.11 与 3.13（CI 双平台） |
| 依赖锁 | `.github/workflows/requirements-p3b.txt`（`--require-hashes`） |
| 源码根 | `E:/llmwiki/trade-price-only-validation-v1`（`src/chanlun_trader`） |
| 数据根 | `E:/new_tdx_mock/vipdoc`（显式只读）；本工作区 `data/` |
| 结果根 | `reports/price_only_validation_v1/`（独立，不覆盖既有报告） |
| 读取器审计写入 | `data/research/audit/physical_read_audit.jsonl`（本工作区，非旧工作区） |

**旧工作区未动**：`E:/llmwiki/trade-system-contract-port-v1` 的 HEAD `d14178f`、分支、19 项既有改动保持原样；其 `data/` 未迁移、未整体复制。

**数据根不是导入路径**：`PYTHONPATH` 仅含 `src` 与 `tests/isolation`，数据根不在其中。

---

## 1. 访问事故防复发（先于任何数据操作）

上一轮盘点发生 gbbq 整文件越界物化（物化 192797 条，datetime 达 20260930 > `RESEARCH_END=20250731`）。本轮最小防复发：

**实现**：`src/chanlun_trader/price_only_scope.py`

- `is_forbidden_gbbq_path()` 按**真实路径身份**判定：落在真实 gbbq 根（`E:/new_tdx_mock/T0002/hq_cache`）之下且基名为 `gbbq`/`gbbq.map`/`gbbq.csv`，或落在真实缓存根（`data/cache`）之下的 `gbbq.csv`，或命中显式清单 `CHANLUN_FORBIDDEN_GBBQ_PATHS`。
- **接入实际访问链**：`TdxData._load_gbbq()` 在**任何 open 之前**调用守卫；覆盖直接 `_load_gbbq` 与经 `TdxData` 构造的间接调用。
- `assert_gbbq_read_disabled()` 拒绝通过 `CHANLUN_ALLOW_GBBQ_READ=1` 放开。

**为什么按路径而不按文件名**：临时目录里的合成 `gbbq.csv` 是既有测试夹具，按名禁止会误伤，进而逼出"用 mock 假装安全"的假证据。

**证明方式**（不依赖 mocked provider）：

| 测试 | 证明 |
| --- | --- |
| `test_guard_rejects_before_real_open` | 用 `builtins.open` 探针断言拒绝时 `opened == []` |
| `test_load_gbbq_rejects_real_path_without_reading` | `_load_gbbq` 走真实路径时同样在读取前拒绝 |
| `test_load_gbbq_rejects_real_cache_csv` | 全量缓存 `gbbq.csv` 同样禁止 |
| `test_synthetic_fixture_paths_are_not_forbidden` | 合成夹具不被误伤 |
| `test_normal_price_path_still_works` | 正对照：正常价格路径不受影响 |
| `test_env_override_is_refused` | 环境变量不能放开 |

**实际生效证据**：`tests/pit/test_qfq_pit_safety.py` 原会真实读取 gbbq（此前 29.85s 通过）。接入守卫后 0.56s 内失败于 `FORBIDDEN_GBBQ_ACCESS`——**未读取文件**。该测试已改为显式 `skip`（`TASK_FORBIDS_REAL_GBBQ_ACCESS`），不伪装通过。

**边界声明（不夸大）**：这是**进程内路径守卫**，不是 OS 沙箱，不能约束任意恶意 Python。它只覆盖本任务实际使用的调用链。**未**实现 gbbq 的物理限窗读取——那需要先验证日期索引、排序与加密记录结构，属后续独立范围。

---

## 2. 盘点矛盾更正（10 项，原件保留）

原件原样保留在 `reports/actual_data_inventory_20260922/`（未修改）。更正写入 `INVENTORY_CORRECTIONS_V1.json`。

| 编号 | 原声明的问题 | 更正 |
| --- | --- | --- |
| C1 | "本轮未读封存期任何数据"的无条件断言 | 与事实矛盾。必须分列：原件字节读取（整文件）、解码/物化（192797 条）、返回、事故统计、策略/绩效用途（未使用）。不得把"未用于回测"写成"未读取" |
| C2 | "9521" 被当作数据规模 | 9521 是 **.day 文件数**（sh 4835 + sz 4337 + bj 349），非已认证证券数。两个实读样本各 382 行，日志合计 764 行 |
| C3 | 4,172,730 被当作可用数据量 | 那是 Parquet **元数据总行数**；实际获准切片 1,709,439 行 / 4536 证券。不能外推 |
| C4 | 5m 行数被表述为已核验 | 116,422,452 是**元数据计数**；冻结清单 116,422,256，差异 196 未解释。末分区只读 trade_date/symbol，不能称"全套 OHLCVA 逐值验证" |
| C5 | PIT 字段存在被当作完整性证明 | 字段存在**不**证明全市场历史完整性、退市齐备、逐日 ST/停牌齐备 |
| C6 | 整型成交量被当作单位已证明 | 保持 `UNIT_IDENTITY_UNVERIFIED`。绝对量/容量/均价用途不能据此通过 |
| C7 | "没有找到"未限定范围 | 限定到实际搜索的根、文件类型、字段与时间。不能据文件名断言全库无复权列 |
| C8 | 流通股本/复权被当作所有账户回测共同前置 | 不设为共同前置，只冻结相应能力 |
| C9 | ST/停牌被表述为"只有显式用该因子的策略才需要" | 真实订单的**交易资格与价格限制**也依赖它；纯公式验收不依赖 |
| C10 | prev_close 缺失处理未按指标合同区分 | 按各指标已批准公式处理，区分窗口首行/数据集首行/上市首日；要求真实前收而缺失判 UNKNOWN |

**未因更正而重扫数百万行情**；证据不足项标 `NOT_ESTABLISHED`（全库列结构扫描、供应商服务可用性、5m 差异根因、全量 .day 逐文件质量）。

---

## 3. A0：纯公式与条件验收（本轮主要交付）

### 3.1 范围

对当前 main 中**已经实现且只需 OHLC/前收/成交量/成交额**的 51 个指标建立增量验收。不限定 MACD/KDJ，不新增 KAMA/Supertrend 等未实现家族。

### 3.2 本轮新增独立 oracle（25 项）

`DEMA`、`TEMA`、`CCI`、`NATR`、`PSY`、`DONCHIAN`、`KELTNER`、`ROLLING_VOLATILITY`、`HISTORICAL_RETURN`、`PRICE_EXTREMES`、`PRIOR_BREAKOUT`、`DRAWDOWN_FROM_PEAK`、`MACD_HIST_RAW`、`TRIX`、`VOLUME_MA`、`AMOUNT_MA`、`RVOL_PRIOR`、`RVOL_INCL_CURRENT`、`ACCUMULATION_DISTRIBUTION`、`CHAIKIN_MONEY_FLOW`、`PVT`、`VWAP_SESSION_PROXY`、`MFI`、`ROLLING_SLOPE`、`TRUE_RANGE`

实现位于 `tests/indicators_v2/_oracle_price_only_v1.py`（28 个朴素函数），**不导入被测实现**，避免循环自证。

### 3.3 测试规模

| 套件 | 项数 |
| --- | --- |
| 公式增量（`test_price_only_formula_increment_v1.py`） | 158 |
| 条件消费（`test_price_only_condition_consumption_v1.py`） | 41 |
| 访问边界（`test_gbbq_access_guard_v1.py` + `test_bounded_read_boundary_v1.py`） | 20 |
| 真实 RAW 小样本（`test_real_raw_sample_v1.py`） | 11 |
| **本轮新增合计** | **230** |

覆盖要求：至少两个参数设置；常数/递增/递减/振荡/跳变/缺口/零成交量/非有限值/短于预热；追加未来尾段不改变历史值；条件 TRUE/FALSE/UNKNOWN 正反例齐备；不以全部拒单制造通过；入口测试实际消费指标输出（不以 list-indicators 作证）。

### 3.4 本轮发现的真实缺陷（已复现并最小修复）

**PSY 恒为 100%。**

根因：分子 `counts`（窗口内非 NaN 计数）与分母 `totals`（`isfinite` 的 1/NaN 之和）**数学恒等**，故 `counts/totals` 恒为 1.0，PSY 恒输出 100%，完全丢失"上涨根数占比"语义。

复现：交替涨跌序列（约 50% 上涨）下 PSY 恒为 100.0，期望约 50%。

修复：分子改为 `sum(up)`（上涨根数），分母改为 `count(up)`（可比较根数，含上涨与下跌）。

回归：新增三项测试（交替序列不恒为 100 / 单调上涨仍为 100 / 真实 RAW 上不恒为 100）。**既有测试全部通过**，说明原测试未覆盖振荡序列下的 PSY。

---

## 4. A1：真实 RAW 数值小样本

**状态：PASS**

| 项 | 值 |
| --- | --- |
| 证券 | `600000.SH`、`000001.SZ` |
| 日期 | 2024-01-02 至 2024-07-31 |
| 来源 | TDX `.day`，经 `read_day_file_range` 有界读取 |
| 每证券物化行数 | 140 |
| `max_date_materialized` | 20240731（< `FINAL_TEST_START=20250801`） |
| 封存期触碰 | 否 |

**前置证明**（任务第 6 节：先读取器边界测试，后实际读取；不得用再读真实文件"证明"上次未越界）：

`tests/price_only_scope/test_bounded_read_boundary_v1.py` 在**合成 .day 文件**上证明：请求区间外记录（含封存期）不被物化；请求越过封存期被拒绝；物理读取记录数不随文件总长线性增长；默认 `end_date` 即 `RESEARCH_END`。

**对账**：8 个指标（EMA/DEMA/TEMA/TRUE_RANGE/NATR/PSY/CCI/MACD_HIST_RAW）与独立朴素参考在真实 RAW 上逐值一致。

**标记**：

```
PRICE_VIEW = RAW_CALLER_SERIES
CORPORATE_ACTION_NORMALIZED = false
HISTORICAL_TRADE_ELIGIBILITY_CERTIFIED = false
PROFITABILITY_VALIDATED = false
```

**未执行**：策略买卖条件搜索、收益排序、参数择优、实盘信号清单、真实账户回测、绩效与胜率。

**prev_close 处理**：切片首根无切片内前收（真实前收在窗口外），置 NaN，**不越界取窗口外数据补足**。

**RAW 跳变说明**：可能来自公司行动或真实行情；本任务不解读为交易机会，也不暗示缺口已解决。

---

## 5. 最终声明

```
FORMULA_ACCEPTANCE = 25 项新增独立 oracle 逐值通过；1 项真实缺陷（PSY）已修复
PUBLIC_SYNTHETIC_CONSUMPTION = 41 项条件消费 + 25 项经公开服务进入合成账户链
REAL_RAW_NUMERIC_SAMPLE = PASS
REAL_PERFORMANCE_EXPERIMENTS = 0
REAL_ACCOUNT_CERTIFICATION = NOT_ESTABLISHED
Gbbq_INCIDENT = HISTORICAL_ACCESS_DISCLOSED_NOT_ERASED
MINUTE_FREEZE_RECONCILIATION = OPEN_NOT_IN_THIS_SCOPE
```

---

## 6. 保留的 OPEN 清单

| 项 | 状态 |
| --- | --- |
| 5m 196 行差异对账 | `OPEN_NOT_IN_THIS_SCOPE`（未来使用该数据集前必须对账，不改写冻结清单凑平） |
| gbbq 物理限窗读取实现 | OPEN（需先验证日期索引/排序/加密记录结构） |
| 历史流通股本（换手率/容量模型） | OPEN（只冻结相应能力） |
| ST/停牌落盘（账户级交易资格） | OPEN |
| 复权落盘或受控事件转换 | OPEN（无证据时不假定可用） |
| 全库列结构扫描 | `NOT_ESTABLISHED` |
| 数据供应商服务可用性 | `NOT_ESTABLISHED`（本机单次探测不足） |
| 分钟数据盘点、多周期撮合 | 暂缓 |
| AI 盈利策略搜索、真实 Trial、Paper、部署 | 暂缓 |

---

## 7. 边界

不 merge、不 auto-merge、不 force push、不部署；不读封存期、不跑 D、不搜索盈利策略、不改预算/Trial/研究授权、不运行 Paper 或实盘、不动受保护旧目录。

真实行情值、事故中解码的封存期记录与完整本机路径日志**不推送 GitHub**；仓库内采用脱敏摘要与已授权切片的身份记录。
