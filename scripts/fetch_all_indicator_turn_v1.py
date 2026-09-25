"""Freeze the bounded BaoStock daily turnover observations for the pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import baostock as bs
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_all_indicator_strategy_v1 import END_DATE, START_DATE, SYMBOLS, sha256_file


FIELDS = "date,code,volume,turn,tradestatus"


def fetch() -> pd.DataFrame:
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BAOSTOCK_LOGIN_FAILED:{login.error_code}:{login.error_msg}")
    rows = []
    try:
        for symbol in SYMBOLS:
            provider_code = ("sz." if symbol.endswith(".SZ") else "sh.") + symbol[:6]
            response = bs.query_history_k_data_plus(
                provider_code, FIELDS,
                start_date=pd.Timestamp(str(START_DATE)).strftime("%Y-%m-%d"),
                end_date=pd.Timestamp(str(END_DATE)).strftime("%Y-%m-%d"),
                frequency="d", adjustflag="3",
            )
            if response.error_code != "0":
                raise RuntimeError(f"BAOSTOCK_QUERY_FAILED:{symbol}:{response.error_code}:{response.error_msg}")
            while response.next():
                item = dict(zip(response.fields, response.get_row_data()))
                rows.append({
                    "symbol": symbol, "date": int(item["date"].replace("-", "")),
                    "provider_code": item["code"], "volume": float(item["volume"]),
                    "turn": float(item["turn"]), "tradestatus": int(item["tradestatus"]),
                })
    finally:
        bs.logout()
    frame = pd.DataFrame(rows).sort_values(["symbol", "date"]).reset_index(drop=True)
    if (frame.empty or frame.duplicated(["symbol", "date"]).any()
            or not frame["symbol"].isin(SYMBOLS).all()
            or not frame["date"].between(START_DATE, END_DATE).all()
            or not frame["tradestatus"].eq(1).all()
            or frame[["volume", "turn"]].isna().any().any()
            or not frame["volume"].gt(0).all()
            or not frame["turn"].ge(0).all()):
        raise ValueError("BAOSTOCK_TURN_SNAPSHOT_INVALID")
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    path = args.output
    manifest_path = path.with_suffix(".manifest.json")
    if path.exists() or manifest_path.exists():
        raise FileExistsError("TURN_SNAPSHOT_ALREADY_EXISTS")
    frame = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    canonical = frame.to_json(orient="records", force_ascii=False)
    manifest = {
        "dataset_id": "ALL_51_BAOSTOCK_TURN_20231009_20240731_V1",
        "provider": "BaoStock", "api": "query_history_k_data_plus",
        "fields": FIELDS.split(","), "frequency": "d", "adjustflag": "3",
        "requested_dates": [START_DATE, END_DATE], "symbols": list(SYMBOLS),
        "rows_by_symbol": {key: int(value) for key, value in frame.groupby("symbol").size().items()},
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "provider_turn_unit": "percent", "historical_available_at_verified": False,
        "normalized_records_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "parquet_sha256": sha256_file(path),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(path), "manifest": str(manifest_path),
                      "rows": len(frame), "sha256": manifest["parquet_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
