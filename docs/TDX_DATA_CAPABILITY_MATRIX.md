# TDX DATA CAPABILITY MATRIX

生成时间：2026-08-18
数据层状态：`TDX_DATA_LAYER_STATUS = READY`（日线 + 专业数据；5分钟 NOT_READY）

| Dataset | Skill | Source | History | PIT | Code Adapter | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 日线 OHLCVA | tdx-tq-local / tdx-quant | TQ `get_market_data` + 本地 `.day` | 全历史 | 安全（T收盘后） | TDXDailyProvider / TdxData | READY |
| 5分钟 OHLCVA | tdx-quant | TQ `refresh_kline(5m)` + vipdoc/minline | 本地无 | — | TDX5MinProvider（占位） | UNAVAILABLE |
| 龙虎榜（个股） | tdx-tq-local | `get_gpjy_value` GP02/08/09/17/18/37/42 | 2023-07-17+ | 安全（T收盘后，T+1可交易） | TDXLHBProvider | READY |
| 龙虎榜（市场） | tdx-tq-local | `get_scjy_value` SC16/17/18/19 | 全历史 | 安全（T收盘后） | TDXMarketSentimentProvider | READY |
| 涨停/炸板/连板（个股） | tdx-tq-local | `get_gpjy_value` GP14/15/22/24/33/34 | 2016-09-26+（涨停状态），首封时间2016-03+，最大封单2020-07+ | 安全（T收盘后） | TDXLimitUpProvider | READY |
| 涨停/连板/高度（市场） | tdx-tq-local | `get_scjy_value` SC03/04/15/23/24/30/33/35/36 | 2016-09-26+ / 2018-03-19+ | 安全（T收盘后） | TDXMarketSentimentProvider | READY |
| 市场情绪 | tdx-tq-local | SC 系列 15 字段 | 2016-09-26+ | 安全（T收盘后） | TDXMarketSentimentProvider | READY |
| 封单 | tdx-tq-local | GP15/22/24、SC33 | 同上 | 安全（T收盘后） | TDXLimitUpProvider | READY |
| 财务（PIT） | tdx-tq-local | `get_financial_data` report_type=announce_time | 有公告时间 | 安全（公告日盘后） | TDXFinanceProvider | READY |
| 行业/概念当前成分 | tdx-tq-local | `get_relation` / `get_stock_list_in_sector` / `get_stock_info` | 仅当前快照 | 当前可用；历史回测 PIT_UNSAFE | TDXIndustryProvider | PARTIAL |
| 行业/概念历史成分 | — | 无历史文件 | — | — | 未实现 | UNAVAILABLE |
| 资金流（主力/超大单/大单） | tdx-tq-local | `get_more_info` Zjl/Zjl_HB（当前快照）；GP/SC 无历史分单 | 无 PIT 历史 | PIT_UNSAFE | 未实现 | ONLINE_ONLY |
| 大宗交易 | tdx-tq-local | GP04 / SC11 | 有历史 | 安全（盘后） | 未实现 | READY（未固化） |
| 融资融券 | tdx-tq-local | GP03/11/12/13、SC01/25 | 有历史 | 安全（盘后） | 未实现 | READY（未固化） |
| 陆股通 | tdx-tq-local | GP06/07、SC02/20 | 有历史 | 安全（盘后） | 未实现 | READY（未固化） |

关键实现细节：TQ HTTP 专业数据方法（`get_*_value`）实际要求 `table_list` 字段，而不是文档中的 `field_list`。本项目 TQClient 已做自动翻译，调用方统一使用 `field_list` 语义。
