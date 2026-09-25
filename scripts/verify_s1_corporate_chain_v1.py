"""Run the frozen 51-indicator strategy through real cash-dividend account dates."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.probe_all_indicator_strategy_v1 import approved_existing_file, sha256_file
from scripts.verify_fixed_strategy_state_v1 import verify


ACCOUNT_END_DATE = 20240731
ACTION_REPORT = ROOT / "reports/s1_trusted_baseline_20260925/BAOSTOCK_ACTIONS.json"
REPORT_PATH = ROOT / "reports/s1_trusted_baseline_20260925/CORPORATE_CHAIN.json"
TAX_SOURCE = "https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html"
EXPECTED = {
    "000001.SZ": {
        "record": "2024-06-13", "effective": "2024-06-14",
        "payment": "2024-06-14", "gross": "0.719",
        "published": "2024-06-06",
        "source": "https://static.cninfo.com.cn/finalpage/2024-06-06/1220273257.PDF",
    },
    "600000.SH": {
        "record": "2024-07-17", "effective": "2024-07-18",
        "payment": "2024-07-18", "gross": "0.321",
        "published": "2024-07-11",
        "source": "https://news.spdb.com.cn/investor_relation/company_report/202407/P020240710607607691605.pdf",
    },
}


def _day(value: str) -> int:
    return int(value.replace("-", ""))


def checked_events(snapshot: dict) -> tuple[dict, ...]:
    if (snapshot.get("provider") != "BaoStock"
            or snapshot.get("api") != "query_dividend_data"
            or snapshot.get("account_dates") != [20240201, ACCOUNT_END_DATE]
            or snapshot.get("symbols") != list(EXPECTED)
            or snapshot.get("queried_report_years") != [2022, 2023, 2024]
            or snapshot.get("query_year_type") != "report"):
        raise ValueError("S1_ACTION_SOURCE_SCOPE_CONFLICT")
    records = snapshot.get("records")
    if not isinstance(records, list) or snapshot.get("response_records_sha256") != hashlib.sha256(
            json.dumps(records, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")).hexdigest():
        raise ValueError("S1_ACTION_SOURCE_RECORDS_CONFLICT")
    counts = {f"{symbol}:{year}": 0 for symbol in EXPECTED for year in (2022, 2023, 2024)}
    for record in records:
        key = f"{record['symbol']}:{record['report_year']}"
        if key not in counts:
            raise ValueError("S1_ACTION_SOURCE_RECORDS_CONFLICT")
        counts[key] += 1
    if counts != snapshot.get("query_counts"):
        raise ValueError("S1_ACTION_SOURCE_COUNTS_CONFLICT")
    rows = snapshot.get("account_window_events", [])
    window_rows = [record for record in records if any(
        day and 20240201 <= _day(day) <= ACCOUNT_END_DATE
        for day in (record["dividRegistDate"], record["dividOperateDate"],
                    record["dividPayDate"], record["dividStockMarketDate"]))]
    if rows != window_rows:
        raise ValueError("S1_ACTION_WINDOW_SELECTION_CONFLICT")
    if len(rows) != len(EXPECTED) or {row["symbol"] for row in rows} != set(EXPECTED):
        raise ValueError("S1_ACTION_EVENT_COVERAGE_CONFLICT")
    events = []
    for row in sorted(rows, key=lambda item: item["symbol"]):
        expected = EXPECTED[row["symbol"]]
        if (row["dividRegistDate"] != expected["record"]
                or row["dividOperateDate"] != expected["effective"]
                or row["dividPayDate"] != expected["payment"]
                or row["dividCashPsBeforeTax"] != expected["gross"]
                or float(row["dividStocksPs"] or 0) != 0
                or float(row["dividReserveToStockPs"] or 0) != 0
                or row["dividStockMarketDate"]):
            raise ValueError(f"S1_ACTION_TERMS_CONFLICT:{row['symbol']}")
        events.append({
            "event_id": f"{row['symbol']}:2023_ANNUAL_CASH_DIVIDEND",
            "symbol": row["symbol"], "event_type": "CASH_DIVIDEND",
            "record_date": _day(expected["record"]),
            "effective_date": _day(expected["effective"]),
            "payment_date": _day(expected["payment"]),
            "terms": {"cash_per_share": float(expected["gross"]),
                      "tax_rule": {"kind": "DEFERRED_INDIVIDUAL_2015_101",
                                   "source": TAX_SOURCE}},
            "units": "CNY_PER_SHARE", "source": expected["source"],
            "source_published_at": expected["published"],
        })
    return tuple(events)


def _economic_hash(result: dict) -> str:
    chain = result["gated_chain"]
    material = {"trades": chain["trades"],
                "account": chain["independent_account_checks"],
                "actions": chain["corporate_account"]}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def run(daily_path: Path, turn_path: Path, states_path: Path,
        original_actions_path: Path, turn_manifest_path: Path,
        historical_states_path: Path, action_snapshot_path: Path = ACTION_REPORT) -> dict:
    action_snapshot_path = approved_existing_file(action_snapshot_path)
    snapshot = json.loads(action_snapshot_path.read_text(encoding="utf-8"))
    events = checked_events(snapshot)
    action_hash = sha256_file(action_snapshot_path)
    args = (daily_path, turn_path, states_path, original_actions_path,
            turn_manifest_path, historical_states_path)
    result = verify(*args, account_end_date=ACCOUNT_END_DATE,
                    corporate_events=events, corporate_actions_hash=action_hash)
    if result["blockers"] or result["gated_chain"] is None:
        result["corporate_step_status"] = "BLOCKED"
        return result
    repeated = verify(*args, account_end_date=ACCOUNT_END_DATE,
                      corporate_events=events, corporate_actions_hash=action_hash)
    first_hash, repeated_hash = _economic_hash(result), _economic_hash(repeated)
    result["corporate_action_source"] = {
        "baostock_snapshot_sha256": action_hash,
        "official_issuer_announcements": {symbol: item["source"] for symbol, item in EXPECTED.items()},
        "individual_tax_policy": TAX_SOURCE,
        "account_profile": "INDIVIDUAL_A_SHARE_STANDARD",
        "verifier_sha256": sha256_file(Path(__file__)),
    }
    result["economic_replay_sha256"] = first_hash
    result["economic_replay_identical"] = first_hash == repeated_hash
    if not result["economic_replay_identical"]:
        result["blockers"].append("CORPORATE_ACCOUNT_REPLAY_MISMATCH")
    result["corporate_step_status"] = "PASSED_MODELED" if not result["blockers"] else "BLOCKED"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-parquet", type=Path, required=True)
    parser.add_argument("--turn-parquet", type=Path, required=True)
    parser.add_argument("--states-parquet", type=Path, required=True)
    parser.add_argument("--original-actions-json", type=Path, required=True)
    parser.add_argument("--turn-manifest-json", type=Path, required=True)
    parser.add_argument("--historical-states-parquet", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve() != REPORT_PATH.resolve():
        parser.error(f"--report must be {REPORT_PATH}")
    result = run(args.daily_parquet, args.turn_parquet, args.states_parquet,
                 args.original_actions_json, args.turn_manifest_json,
                 args.historical_states_parquet)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
                           encoding="utf-8")
    print(json.dumps({"corporate_step_status": result["corporate_step_status"],
                      "blockers": result["blockers"], "report": str(REPORT_PATH)},
                     ensure_ascii=False))
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
