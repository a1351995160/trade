"""Verify the fixed pilot's modeled state availability and execution-state gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.probe_all_indicator_strategy_v1 import (
    ACCOUNT_END_DATE, ACCOUNT_START_DATE, END_DATE, START_DATE, SYMBOLS,
    approved_existing_file, known_bool, probe, run_chain, sha256_file,
)
from chanlun_trader.research.io_safety import GuardedResearchReader


HISTORICAL_STATE_ROOT = Path(
    "E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/materialized-v3"
).resolve()
REPORT_PATH = ROOT / "reports/s1_trusted_baseline_20260925/STATE_GATE.json"
STATE_FIELDS = (
    "listed", "delisted", "universe_member", "eligibility_status",
    "st_status", "suspension_status", "board",
)


def _historical_path(path: Path, fixture_root: Path | None) -> Path:
    resolved = path.resolve(strict=True)
    roots = (HISTORICAL_STATE_ROOT,)
    if fixture_root is not None:
        roots = (*roots, fixture_root.resolve(strict=True))
    if not resolved.is_file() or not any(resolved.is_relative_to(root) for root in roots):
        raise ValueError(f"HISTORICAL_STATE_PATH_NOT_ALLOWED:{path}")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_eligible(row) -> bool:
    return (known_bool(row["listed"], True) and known_bool(row["delisted"], False)
            and known_bool(row["universe_member"], True)
            and row["eligibility_status"] == "ELIGIBLE"
            and row["st_status"] == "NORMAL"
            and row["suspension_status"] == "TRADING")


def verify(daily_path: Path, turn_path: Path, states_path: Path,
           actions_path: Path, turn_manifest_path: Path,
           historical_states_path: Path, *, fixture_root: Path | None = None,
           account_end_date: int = ACCOUNT_END_DATE,
           corporate_events: tuple[dict, ...] | None = None,
           corporate_actions_hash: str | None = None) -> dict:
    """Use upstream state provenance without granting strategy qualification."""
    daily_path = approved_existing_file(daily_path, fixture_root=fixture_root)
    turn_path = approved_existing_file(turn_path, fixture_root=fixture_root)
    states_path = approved_existing_file(states_path, fixture_root=fixture_root)
    actions_path = approved_existing_file(actions_path, fixture_root=fixture_root)
    turn_manifest_path = approved_existing_file(turn_manifest_path, fixture_root=fixture_root)
    historical_states_path = _historical_path(historical_states_path, fixture_root)

    pilot = probe(daily_path, turn_path, states_path, actions_path,
                  turn_manifest_path, fixture_root=fixture_root)
    blockers = list(pilot["blockers"])
    audit = []
    reader = GuardedResearchReader(audit_sink=audit.append)
    daily = reader.read_parquet(
        daily_path, columns=["symbol", "date", "open", "high", "low", "close",
                             "volume", "amount", "prev_close"],
        start_date=START_DATE, end_date=END_DATE,
    )
    daily = daily.loc[daily.symbol.isin(SYMBOLS)]
    states = reader.read_parquet(
        states_path, columns=["symbol", "trade_date", *STATE_FIELDS],
        date_column="trade_date", start_date=ACCOUNT_START_DATE,
        end_date=account_end_date,
    )
    states = states.loc[states.symbol.isin(SYMBOLS)]
    historical = reader.read_parquet(
        historical_states_path,
        columns=["symbol", "trade_date", "available_at", "source_lineage", *STATE_FIELDS],
        date_column="trade_date", date_format="iso",
        start_date=20240131, end_date=account_end_date,
    )
    historical = historical.loc[historical.symbol.isin(SYMBOLS)].copy()
    historical["trade_date"] = historical.trade_date.str.replace("-", "", regex=False).astype(int)
    if (historical.duplicated(["symbol", "trade_date"]).any()
            or states.duplicated(["symbol", "trade_date"]).any()):
        blockers.append("HISTORICAL_STATE_DUPLICATE")

    days = sorted(int(day) for day in daily.date.unique())
    prior = {days[index]: days[index - 1] for index in range(1, len(days))}
    next_day = {days[index]: days[index + 1] for index in range(len(days) - 1)}
    state_issues = []
    decision_states = {}
    if not blockers:
        expected_days = {day for day in days if ACCOUNT_START_DATE <= day <= account_end_date}
        for symbol in SYMBOLS:
            actual_days = set(states.loc[states.symbol == symbol, "trade_date"].astype(int))
            historical_days = set(historical.loc[historical.symbol == symbol, "trade_date"].astype(int))
            if actual_days != expected_days:
                state_issues.append(f"EXECUTION_STATE_COVERAGE_INVALID:{symbol}")
            if not expected_days | {prior[ACCOUNT_START_DATE]} <= historical_days:
                state_issues.append(f"HISTORICAL_STATE_COVERAGE_INVALID:{symbol}")
        current = states.set_index(["symbol", "trade_date"]).sort_index()
        history = historical.set_index(["symbol", "trade_date"]).sort_index()
        for symbol in SYMBOLS:
            for day in sorted(int(d) for d in states.loc[states.symbol == symbol, "trade_date"]):
                key = (symbol, day)
                if key not in history.index:
                    state_issues.append(f"STATE_SOURCE_DAY_MISSING:{symbol}:{day}")
                    continue
                actual, source = current.loc[key], history.loc[key]
                if any(pd.isna(actual[field]) or pd.isna(source[field])
                       or actual[field] != source[field] for field in STATE_FIELDS):
                    state_issues.append(f"STATE_SOURCE_CONTENT_CONFLICT:{symbol}:{day}")
            for day in sorted(int(d) for d in states.loc[states.symbol == symbol, "trade_date"]):
                previous = prior.get(day)
                key = (symbol, previous)
                if previous is None or key not in history.index:
                    state_issues.append(f"DECISION_PRIOR_STATE_MISSING:{symbol}:{day}")
                    continue
                decision_states[(symbol, day)] = history.loc[key]
        for (symbol, day), row in history.iterrows():
            try:
                lineage = json.loads(row["source_lineage"])
                available = pd.Timestamp(row["available_at"])
                expected = (pd.Timestamp(str(next_day[day]), tz="Asia/Shanghai")
                            + pd.Timedelta(hours=9, minutes=30)) if day in next_day else None
                if (lineage.get("history") != "baostock.query_history_k_data_plus"
                        or lineage.get("all_stock") != "baostock.query_all_stock"
                        or available.tzinfo is None
                        or (available != expected if expected is not None else
                            available <= pd.Timestamp(str(day), tz="Asia/Shanghai")
                            + pd.Timedelta(hours=15, minutes=30))):
                    raise ValueError("state provenance or modeled timestamp differs")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                state_issues.append(f"STATE_AVAILABILITY_OR_LINEAGE_INVALID:{symbol}:{day}")
        if state_issues:
            blockers.append("HISTORICAL_STATE_QUALIFICATION_FAILED")

    rejected = []
    gated = None
    same_economic_result = None
    if not blockers:
        decisions = {}
        for symbol in SYMBOLS:
            decisions[symbol] = []
            for item in pilot["decision_trace"][symbol]:
                day = item["date"]
                if not ACCOUNT_START_DATE <= day <= account_end_date:
                    continue
                if item["decision_at_close"] == "BUY":
                    state = decision_states.get((symbol, day))
                    timestamp = pd.Timestamp(str(day), tz="Asia/Shanghai") + pd.Timedelta(hours=15, minutes=30)
                    if (state is None or not _state_eligible(state)
                            or pd.Timestamp(state["available_at"]) > timestamp):
                        rejected.append({"symbol": symbol, "signal_date": day,
                                         "reason": "PRIOR_STATE_NOT_KNOWN_ELIGIBLE"})
                        continue
                decisions[symbol].append(item)
        gated = run_chain(
            daily, decisions, pilot["strategy"], sha256_file(daily_path, fixture_root=fixture_root),
            sha256_file(turn_path, fixture_root=fixture_root),
            sha256_file(states_path, fixture_root=fixture_root),
            corporate_actions_hash or sha256_file(actions_path, fixture_root=fixture_root),
            execution_states=states, historical_states_hash=_sha256(historical_states_path),
            account_end_date=account_end_date, corporate_events=corporate_events,
        )
        if account_end_date == ACCOUNT_END_DATE and corporate_events is None:
            same_economic_result = (
                gated["trades"] == pilot["chain"]["trades"]
                and gated["independent_account_checks"] == pilot["chain"]["independent_account_checks"]
            )
        if gated["issues"]:
            blockers.append("STATE_GATED_ACCOUNT_RECONCILIATION_FAILED")
        if same_economic_result is False and not rejected:
            blockers.append("NORMAL_STATE_EXECUTION_PARITY_FAILED")

    return {
        "purpose": "FIXED_STRATEGY_S1_STATE_QUALIFICATION_DIAGNOSTIC",
        "strategy_id": pilot["strategy"]["strategy_id"],
        "source_code": {
            "git_head": pilot["code_head"],
            "sha256": {str(path.relative_to(ROOT)): _sha256(path) for path in (
                ROOT / "scripts/probe_all_indicator_strategy_v1.py",
                ROOT / "scripts/verify_fixed_strategy_state_v1.py",
                ROOT / "src/chanlun_trader/engine/individual_dividend_accounting_v1.py",
                ROOT / "src/chanlun_trader/engine/corporate_accounting_v1.py",
                ROOT / "src/chanlun_trader/engine/corporate_action_engine_v1.py",
                ROOT / "src/chanlun_trader/engine/custom_indicators_v2.py",
                ROOT / "src/chanlun_trader/engine/engine.py",
                ROOT / "src/chanlun_trader/engine/indicator_registry_v2.py",
                ROOT / "src/chanlun_trader/engine/indicators_v2.py",
                ROOT / "src/chanlun_trader/engine/ledger.py",
                ROOT / "src/chanlun_trader/engine/official_valuation.py",
                ROOT / "src/chanlun_trader/research/io_safety.py",
                ROOT / "src/chanlun_trader/research_factory/degraded_execution_v2.py",
            )},
        },
        "scope": {"symbols": list(SYMBOLS),
                  "account_dates": [ACCOUNT_START_DATE, account_end_date],
                  "state_availability": "MODELED_NEXT_SESSION_OPEN_NOT_VENDOR_PUBLICATION_TIME"},
        "sources": {"daily_sha256": sha256_file(daily_path, fixture_root=fixture_root),
                    "turn_sha256": sha256_file(turn_path, fixture_root=fixture_root),
                    "states_sha256": sha256_file(states_path, fixture_root=fixture_root),
                    "historical_states_sha256": _sha256(historical_states_path),
                    "actions_sha256": sha256_file(actions_path, fixture_root=fixture_root),
                    "corporate_actions_sha256": corporate_actions_hash,
                    "turn_manifest_sha256": sha256_file(turn_manifest_path, fixture_root=fixture_root)},
        "state_content_parity": (not state_issues and "HISTORICAL_STATE_DUPLICATE" not in blockers
                                 if not pilot["blockers"] else None),
        "state_issues": state_issues,
        "decision_state_rejections": rejected,
        "execution_state_gate": "WIRED" if gated is not None else "NOT_RUN",
        "same_economic_result_as_prior_pilot": same_economic_result,
        "gated_chain": gated,
        "read_audit": [*pilot["read_audit"], *audit],
        "blockers": sorted(set(blockers)),
        "state_step_status": "PASSED_MODELED" if gated is not None and not blockers else "BLOCKED",
        "s1_baseline_status": "NOT_PASSED",
        "remaining_s1_work": [
            "same fixed strategy through the public strategy entry",
            *([] if corporate_events is not None else
              ["real corporate-action account reconciliation beyond the event-free window"]),
            "full-engine checkpoint restart and real suspension/missing-data cases remain unverified",
            "vendor turnover and historical state publication timestamps remain unverified",
            "adjusted indicator features versus raw execution prices remain unverified",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-parquet", type=Path, required=True)
    parser.add_argument("--turn-parquet", type=Path, required=True)
    parser.add_argument("--states-parquet", type=Path, required=True)
    parser.add_argument("--actions-json", type=Path, required=True)
    parser.add_argument("--turn-manifest-json", type=Path, required=True)
    parser.add_argument("--historical-states-parquet", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve() != REPORT_PATH.resolve():
        parser.error(f"--report must be {REPORT_PATH}")
    result = verify(args.daily_parquet, args.turn_parquet, args.states_parquet,
                    args.actions_json, args.turn_manifest_json,
                    args.historical_states_parquet)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"state_step_status": result["state_step_status"],
                      "blockers": result["blockers"], "report": str(REPORT_PATH)},
                     ensure_ascii=False))
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
