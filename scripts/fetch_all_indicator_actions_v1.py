"""Freeze a bounded BaoStock corporate-action absence check for the pilot account."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import baostock as bs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_all_indicator_strategy_v1 import ACCOUNT_END_DATE, ACCOUNT_START_DATE, SYMBOLS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("ACTION_SNAPSHOT_ALREADY_EXISTS")
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BAOSTOCK_LOGIN_FAILED:{login.error_code}:{login.error_msg}")
    response_rows = []
    query_counts = {}
    try:
        for symbol in SYMBOLS:
            code = ("sz." if symbol.endswith(".SZ") else "sh.") + symbol[:6]
            for year in ("2022", "2023", "2024"):
                response = bs.query_dividend_data(code=code, year=year, yearType="report")
                if response.error_code != "0":
                    raise RuntimeError(f"BAOSTOCK_DIVIDEND_QUERY_FAILED:{symbol}:{year}:{response.error_code}")
                rows = []
                while response.next():
                    item = dict(zip(response.fields, response.get_row_data()))
                    rows.append({"symbol": symbol, "report_year": year,
                                 "ex_date": item["dividOperateDate"],
                                 "payment_date": item["dividPayDate"],
                                 "stock_market_date": item["dividStockMarketDate"],
                                 "cash_per_share_before_tax": item["dividCashPsBeforeTax"],
                                 "stock_per_share": item["dividStocksPs"]})
                query_counts[f"{symbol}:{year}"] = len(rows)
                response_rows.extend(rows)
    finally:
        bs.logout()
    in_window = [row for row in response_rows
                 if any(value and ACCOUNT_START_DATE <= int(value.replace("-", "")) <= ACCOUNT_END_DATE
                        for value in (row["ex_date"], row["stock_market_date"]))]
    payload = {
        "dataset_id": "ALL_51_BAOSTOCK_ACTION_SCREEN_20240201_20240531_V2",
        "provider": "BaoStock", "api": "query_dividend_data",
        "query_year_type": "report", "queried_report_years": [2022, 2023, 2024],
        "symbols": list(SYMBOLS), "account_dates": [ACCOUNT_START_DATE, ACCOUNT_END_DATE],
        "query_counts": query_counts, "account_window_events": in_window,
        "response_records_sha256": hashlib.sha256(json.dumps(
            response_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest(),
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "coverage_limit": "BaoStock dividend/stock distribution records only; no independent TDX gbbq cross-check",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(args.output), "events": len(in_window),
                      "query_counts": query_counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
