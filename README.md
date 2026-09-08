# 缠论选股交易系统

基于通达信本地日线数据与缠论知识库，识别一/二/三类买卖点，并进行历史回测、胜率统计。

## 快速开始

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 构建前端（首次或前端代码变更后执行）
cd frontend
npm install
npm run build
cd ..

# 3. 启动 Web UI（默认 http://127.0.0.1:8000）
python scripts/run_ui.py

# 也可以使用命令行
python scripts/run_screener.py 100   # 选股扫描（可选参数：只扫前 N 只股票）
python scripts/run_backtest.py 200   # 回测（可选参数：只回测前 N 只股票）
```

Web 默认只读，不自动恢复研究任务。读取指定工作区可运行 `python scripts/run_ui.py 8000 --research-root <绝对路径>`。本轮 Web 行情、选股和回测入口受策略限制；执行模式、人工治理与测试边界见 [P3-A 验收说明](docs/PHASE3A_EXECUTION_ISOLATION_V1.md)。

回测报告输出到 `data/output/`，交易明细为 CSV，报告为 Markdown。

## 目录结构

```
chanlun-trading-system/
├── config.yaml              # 配置：数据路径、缠论参数、回测参数、过滤器
├── requirements.txt
├── data/
│   ├── cache/               # gbbq 缓存等
│   └── output/              # 回测报告与选股结果
├── src/chanlun_trader/
│   ├── config.py            # 配置加载
│   ├── tdx_data.py          # 通达信 .day/.lc5 读取、gbbq 前复权
│   ├── chan.py              # 缠论核心：包含合并、分型、笔、线段、中枢、背驰、买卖点
│   ├── screener.py          # 选股扫描
│   ├── backtest.py          # 回测引擎
│   ├── metrics.py           # 绩效指标
│   └── report.py            # 报告输出
├── scripts/
│   ├── run_screener.py
│   ├── run_backtest.py
│   └── run_ui.py          # Web UI 启动
├── frontend/              # 前端：Vue 3 + TypeScript + ECharts（Vite 构建）
│   ├── src/
│   └── dist/              # 构建产物，由 FastAPI 托管
└── tests/
```

## 一期已实现

- 通达信本地日线读取（`.day`），沪深 A 股股票池。
- `gbbq` 除权除息解析与前复权。
- K 线包含合并、顶底分型、笔、笔中枢、MACD 背驰。
- 一/二/三类买点与一/二/三类卖点识别。
- 回测：信号日收盘确认、次日开盘成交、T+1、佣金/印花税/滑点、等权仓位、固定止损、最大持有期。
- 绩效统计：胜率、盈亏比、年化、最大回撤、沪深 300 基准对比。

## 二期已实现

- 简化线段识别（`chan.detect_segments`）。
- 大盘环境过滤：上证指数收盘价低于 MA20 时不开新仓（`config.yaml` 中 `index_filter`）。
- 移动止损：浮盈达到阈值后，从最高收盘价回撤一定比例卖出（`config.yaml` 中 `trailing_stop`）。
- 通达信 5 分钟线（`.lc5`）读取与 N 分钟聚合，为多级别联立提供数据支持（当前本机 `minline` 为空，实际运行需先在通达信下载分钟数据）。

## Web UI

- 后端：FastAPI（`src/chanlun_trader/webapp.py`）。
- 前端：Vue 3 + TypeScript + ECharts，Vite 构建（`frontend/`），由 FastAPI 托管 `frontend/dist`。
- 功能：回测参数配置、后台回测与进度显示、绩效卡片、交易明细表、选股信号表、个股 K 线与笔/线段/买卖点标注。

## 测试

```bash
python -m pytest tests -q
```

## 说明

- 本系统为研究用途，不构成投资建议。
- 一期、二期的线段与买卖点均为简化实现，口径见 `docs/chanlun-trading-system.md`。
