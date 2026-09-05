# TDX DATA ARCHITECTURE

## 分层

```
Strategy / Event Study
      ↓ 只调用语义接口（get_lhb / get_sentiment / get_limit_event / get_finance / get_daily）
TDXDataAdapter (src/chanlun_trader/data_adapter.py)
      ↓
TDXProviders (src/chanlun_trader/data/tdx/providers.py)
      ├── TDXDailyProvider
      ├── TDX5MinProvider（NOT_READY 占位）
      ├── TDXMarketSentimentProvider
      ├── TDXLHBProvider
      ├── TDXLimitUpProvider
      ├── TDXFinanceProvider
      ├── TDXIndustryProvider
      ↓
TQClient (src/chanlun_trader/data/tdx/tq_client.py)
   JSON-RPC over HTTP @ http://127.0.0.1:17709/
   health_check / request / request_full / request_paged / retry / timeout / rate_limit / cache
      ↓
通达信客户端 TQ HTTP Service
```

## 数据目录

```
data/tdx/
  raw/                        # TQ 原始 JSON（不可覆盖，按 dataset_start_end_hash 命名）
  clean/                      # Parquet 量化数据
    market_sentiment.parquet
    lhb_events.parquet
    limit_events.parquet
    finance.parquet
    daily.parquet
  data_manifest.parquet       # 数据清单
```

## 已固化 Provider

| Provider | 语义接口 | 底层字段 |
| --- | --- | --- |
| TDXDailyProvider | get_daily(codes,start,end,div) | get_market_data |
| TDX5MinProvider | get_5m(...) | refresh_kline/get_market_data 5m（未就绪） |
| TDXMarketSentimentProvider | get_sentiment(start,end) | SC03/04/15/16/17/18/19/23/24/30/31/33/35/36/39 |
| TDXLHBProvider | get_stock_events / get_events_batch | GP02/08/09/17/18/37/42 |
| TDXLimitUpProvider | get_stock_events / get_events_batch | GP14/15/22/24/33/34 |
| TDXFinanceProvider | get_finance / get_snapshot(code,as_of) | FN 系列，announce_time PIT |
| TDXIndustryProvider | get_industry_current / get_sector_list / get_sector_members | get_stock_info / get_sector_list / get_stock_list_in_sector |

## 选择 HTTP 而非 tqcenter.py 的原因

- HTTP JSON-RPC 服务稳定、可自动化、可缓存、不依赖 `tq.initialize(__file__)` 生命周期。
- 实测 `get_market_data` / `get_scjy_value` / `get_gpjy_value` / `get_financial_data` 均通过 HTTP 正常返回。
- 需要分页时（stock_total_pages > 1），TQClient.request_paged 自动遍历 `stock_page_index`。
