"""Freeze BaoStock dividend records for the predeclared S1 account window."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import baostock as bs


SYMBOLS = ("000001.SZ", "600000.SH")
ACCOUNT_DATES = (20240201, 20240731)
REPORT_YEARS = ("2022", "2023", "2024")
REPORT_PATH = (Path(__file__).resolve().parents[1]
               / "reports/s1_trusted_baseline_20260925/BAOSTOCK_ACTIONS.json")


def _inside(day: str) -> bool:
    return bool(day and ACCOUNT_DATES[0] <= int(day.replace("-", "")) <= ACCOUNT_DATES[1])


def fetch() -> dict:
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BAOSTOCK_LOGIN_FAILED:{login.error_code}:{login.error_msg}")
    records = []
    counts = {}
    try:
        for symbol in SYMBOLS:
            code = ("sz." if symbol.endswith(".SZ") else "sh.") + symbol[:6]
            for year in REPORT_YEARS:
                response = bs.query_dividend_data(code=code, year=year, yearType="report")
                if response.error_code != "0":
                    raise RuntimeError(f"BAOSTOCK_DIVIDEND_QUERY_FAILED:{symbol}:{year}:{response.error_code}")
                count = 0
                while response.next():
                    row = dict(zip(response.fields, response.get_row_data()))
                    records.append({"symbol": symbol, "report_year": year, **row})
                    count += 1
                counts[f"{symbol}:{year}"] = count
    finally:
        bs.logout()
    in_window = [row for row in records if any(_inside(row[field]) for field in (
        "dividRegistDate", "dividOperateDate", "dividPayDate", "dividStockMarketDate"))]
    return {
        "dataset_id": "S1_TWO_BANKS_BAOSTOCK_ACTIONS_20240201_20240731_V1",
        "provider": "BaoStock", "api": "query_dividend_data",
        "query_year_type": "report", "queried_report_years": [int(y) for y in REPORT_YEARS],
        "symbols": list(SYMBOLS), "account_dates": list(ACCOUNT_DATES),
        "query_counts": counts, "records": records,
        "account_window_events": in_window,
        "response_records_sha256": hashlib.sha256(json.dumps(
            records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest(),
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "coverage_limit": "BaoStock dividend and stock-distribution records only; tax depends on holding period and is not certified here",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve() != REPORT_PATH.resolve():
        parser.error(f"--output must be {REPORT_PATH}")
    if REPORT_PATH.exists():
        raise FileExistsError("S1_ACTION_SNAPSHOT_ALREADY_EXISTS")
    result = fetch()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(REPORT_PATH),
                      "window_events": len(result["account_window_events"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
