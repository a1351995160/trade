# DATA CAPABILITY REGISTRY

- Generated: 2026-08-18 23:20:54.629716
- RESEARCH_END: 2025-07-31 (FINAL TEST SEALED)
- REAL_TDX_5M_ADAPTER: READY

| dataset_id | status | freq | earliest | latest | PIT_safe | event_time | available_at | fields |
|---|---|---|---|---|---|---|---|---|
| daily_ohlcva_raw | READY | DAILY | 20210802 | 20260818 | True | TRADE_DATE | T_CLOSE | date,open,high,low,close,volume |
| corporate_action_gbbq | READY | IRREGULAR | 19900301 | 20260812 | True | EX_DATE | EX_DATE_CLOSE | code,datetime,category,hongli_panqianliutong,peigujia_qianzongguben,songgu_qianzongguben |
| benchmark_index_daily | READY | DAILY | 20210802 | 20260818 | True | TRADE_DATE | T_CLOSE | date,open,high,low,close,volume |
| minute_5_ohlcva | READY | 5M | 20210802 | 20260818 | True | BAR_CLOSE | BAR_CLOSE | date,minute,open,high,low,close |
| lhb_professional | ONLINE_ONLY | EVENT | 0 | 0 | True | PUBLISH_DATE | T_CLOSE_OR_ANNOUNCE_CLOSE | GP02/GP08/GP09/GP17/GP18/GP37/GP42 |
| limit_up_ecology | ONLINE_ONLY | EVENT | 0 | 0 | True | PUBLISH_DATE | T_CLOSE_OR_ANNOUNCE_CLOSE | GP14/GP15/GP22/GP24/GP33/GP34/SC03/SC04/SC23/SC30 |
| money_flow | ONLINE_ONLY | EVENT | 0 | 0 | True | PUBLISH_DATE | T_CLOSE_OR_ANNOUNCE_CLOSE | Zjl/Zjl_HB/SC20/SC40 |
| financial_series | ONLINE_ONLY | IRREGULAR | 0 | 0 | True | PUBLISH_DATE | T_CLOSE_OR_ANNOUNCE_CLOSE | FN1/FN4/FN6/FN7/FN230/FN231/FN232/FN314 |

## 5-minute
- symbols=5313, sample={'symbol': '600000.SH', 'earliest_date': 20241009, 'latest_date': 20260818, 'bars': 21792}

## QFQ PIT Acceptance
- ok=17, fail=0

## Rules
- 研究代码禁止读取 >= 2025-08-01 的数据（FinalTestAccessViolation）。
- 正式研究必须使用 qfq_columns_asof(..., as_of=decision_date)，禁止全样本 get_qfq_day。
- status 非 READY 的数据集不得进入正式研究。