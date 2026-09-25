"""Compare the public 51-vote strategy entry with frozen daily and account evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from chanlun_trader.research.io_safety import GuardedResearchReader
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run as public_run
from scripts.probe_all_indicator_strategy_v1 import (
    ACCOUNT_START_DATE, END_DATE, START_DATE, SYMBOLS, approved_existing_file,
)
from scripts.s1_public_entry_strategy_v1 import (
    Fixed51AccountBackend, Fixed51VoteStrategy, input_identity,
)
from scripts.verify_fixed_strategy_state_v1 import STATE_FIELDS, _historical_path, verify
from scripts.verify_s1_corporate_chain_v1 import (
    ACCOUNT_END_DATE, ACTION_REPORT, checked_events,
)


PILOT_REPORT = ROOT / "reports/all_indicator_fixed_strategy_pilot_20260925/PROBE.json"
CORPORATE_REPORT = ROOT / "reports/s1_trusted_baseline_20260925/CORPORATE_CHAIN.json"
REPORT_PATH = ROOT / "reports/s1_trusted_baseline_20260925/PUBLIC_ENTRY_PARITY.json"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _load_bundle(paths: dict[str, Path], source_hashes: dict, audit: list,
                 events: tuple[dict, ...], strategy: Fixed51VoteStrategy) -> dict:
    reader = GuardedResearchReader(audit_sink=audit.append)
    daily = reader.read_parquet(
        paths["daily"], columns=["symbol", "date", "open", "high", "low", "close",
                                 "volume", "amount", "prev_close", "adjustflag"],
        start_date=START_DATE, end_date=END_DATE,
    )
    turn = reader.read_parquet(
        paths["turn"], columns=["symbol", "date", "volume", "turn", "tradestatus"],
        start_date=START_DATE, end_date=END_DATE,
    )
    states = reader.read_parquet(
        paths["states"], columns=["symbol", "trade_date", *STATE_FIELDS],
        date_column="trade_date", start_date=ACCOUNT_START_DATE, end_date=ACCOUNT_END_DATE,
    )
    historical = reader.read_parquet(
        paths["historical_states"],
        columns=["symbol", "trade_date", "available_at", "source_lineage", *STATE_FIELDS],
        date_column="trade_date", date_format="iso",
        start_date=20240131, end_date=ACCOUNT_END_DATE,
    )
    historical = historical.copy()
    historical["trade_date"] = historical.trade_date.str.replace("-", "", regex=False).astype(int)
    daily = daily.loc[daily.symbol.isin(SYMBOLS)].copy()
    turn = turn.loc[turn.symbol.isin(SYMBOLS)].copy()
    states = states.loc[states.symbol.isin(SYMBOLS)].copy()
    historical = historical.loc[historical.symbol.isin(SYMBOLS)].copy()
    bundle = {"daily": daily, "turn": turn, "states": states,
              "historical": historical, "source_hashes": source_hashes,
              "account_end_date": ACCOUNT_END_DATE}
    bundle["input_identity"] = input_identity(bundle, events, strategy.definition["catalog_sha256"])
    return bundle


def _account_material(chain: dict) -> dict:
    return {key: chain[key] for key in (
        "account_dates", "n_account_days", "n_signals", "n_orders", "n_trades",
        "n_no_trade_days", "trades", "orders", "independent_account_checks",
        "corporate_account", "issues",
    )}


def run(daily_path: Path, turn_path: Path, states_path: Path,
        original_actions_path: Path, turn_manifest_path: Path,
        historical_states_path: Path, action_snapshot_path: Path = ACTION_REPORT,
        *, fixture_root: Path | None = None,
        pilot_report_path: Path = PILOT_REPORT,
        corporate_report_path: Path = CORPORATE_REPORT) -> dict:
    paths = {
        "daily": approved_existing_file(daily_path, fixture_root=fixture_root),
        "turn": approved_existing_file(turn_path, fixture_root=fixture_root),
        "states": approved_existing_file(states_path, fixture_root=fixture_root),
        "original_actions": approved_existing_file(original_actions_path, fixture_root=fixture_root),
        "turn_manifest": approved_existing_file(turn_manifest_path, fixture_root=fixture_root),
        "historical_states": _historical_path(historical_states_path, fixture_root),
        "corporate_actions": approved_existing_file(action_snapshot_path, fixture_root=fixture_root),
    }
    frozen_pilot = json.loads(pilot_report_path.read_text(encoding="utf-8"))
    frozen_corporate = json.loads(corporate_report_path.read_text(encoding="utf-8"))
    events = checked_events(json.loads(paths["corporate_actions"].read_text(encoding="utf-8")))
    source_hashes = {
        "daily_sha256": _file_hash(paths["daily"]),
        "turn_sha256": _file_hash(paths["turn"]),
        "states_sha256": _file_hash(paths["states"]),
        "historical_states_sha256": _file_hash(paths["historical_states"]),
        "actions_sha256": _file_hash(paths["original_actions"]),
        "corporate_actions_sha256": _file_hash(paths["corporate_actions"]),
        "turn_manifest_sha256": _file_hash(paths["turn_manifest"]),
    }
    if (source_hashes != frozen_corporate["sources"]
            or frozen_pilot["input"]["sha256"] != source_hashes["daily_sha256"]
            or frozen_pilot["turn_input"]["sha256"] != source_hashes["turn_sha256"]
            or frozen_corporate["blockers"]
            or frozen_corporate["corporate_step_status"] != "PASSED_MODELED"):
        raise ValueError("FROZEN_S1_REFERENCE_OR_INPUT_CHANGED")
    for relative, expected in frozen_corporate["source_code"]["sha256"].items():
        if _file_hash(ROOT / relative) != expected:
            raise ValueError(f"FROZEN_S1_SOURCE_CHANGED:{relative}")
    baseline = verify(
        paths["daily"], paths["turn"], paths["states"],
        paths["original_actions"], paths["turn_manifest"], paths["historical_states"],
        fixture_root=fixture_root, account_end_date=ACCOUNT_END_DATE,
        corporate_events=events, corporate_actions_hash=source_hashes["corporate_actions_sha256"],
    )
    if (baseline["blockers"] or baseline["gated_chain"] is None
            or _account_material(baseline["gated_chain"])
            != _account_material(frozen_corporate["gated_chain"])):
        raise ValueError("LIVE_BASELINE_NO_LONGER_MATCHES_FROZEN_ACCOUNT")
    strategy, backend = Fixed51VoteStrategy(), Fixed51AccountBackend()
    if strategy.definition != frozen_pilot["strategy"]:
        raise ValueError("FROZEN_51_RULE_CHANGED")
    read_audit = []
    bundle = _load_bundle(paths, source_hashes, read_audit, events, strategy)
    plan = prepare(strategy, backend)
    objective = "S1_FIXED_51_PUBLIC_ENTRY_DIAGNOSTIC"
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="s1_public_entry_") as temporary:
        root = Path(temporary)
        budget_path = root / "BUDGET.json"
        budget = SearchBudgetRegistryV1(objective, budget_path)
        budget.register_objective(1)
        budget.consume(budget.reserve("objective", objective))
        governance = StrategyBatchGovernanceV1(
            root / "GOVERNANCE", budget_path, objective, {strategy.strategy_id: plan})
        receipt = governance.confirm(
            {"origin": "USER_EXPLICIT_CURRENT_TASK",
             "statement": "同一套51指标规则走公共策略入口并核对逐日决策和账户结果",
             "approved_plan_ids": {strategy.strategy_id: plan["plan_id"]},
             "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()},
            {"input_identity": bundle["input_identity"],
             "novelty": {strategy.strategy_id: {
                 "allowed": True, "plan_id": plan["plan_id"],
                 "reason": "FROZEN_REFERENCE_DIAGNOSTIC_NOT_SEARCH_CANDIDATE"}}},
        )
        governance.start(strategy.strategy_id)
        outcome = None
        try:
            outcome = public_run(
                strategy, backend, frame=bundle, actions=events,
                input_identity=bundle["input_identity"],
                active_check=lambda: governance.active_execution(strategy.strategy_id),
            )
        finally:
            governance.settle(
                strategy.strategy_id, completed=outcome is not None,
                seconds=round(time.perf_counter() - started, 3),
                result_hash=_json_hash(_account_material(outcome["chain"])) if outcome else "NO_RESULT",
                error=None if outcome else "PUBLIC_ENTRY_EXECUTION_FAILED",
            )
        governance_evidence = {
            "receipt_id": receipt["receipt_id"], "plan_id": plan["plan_id"],
            "input_identity": bundle["input_identity"],
            "execution_budget_used": SearchBudgetRegistryV1(objective, budget_path).used(
                governance.budget_kind, receipt["receipt_id"] + ":" + strategy.strategy_id),
            "purpose": "FROZEN_REFERENCE_DIAGNOSTIC_NOT_SEARCH_CANDIDATE",
        }
    decisions_match = outcome["decisions"] == frozen_pilot["decision_trace"]
    rejections_match = outcome["decision_state_rejections"] == baseline["decision_state_rejections"]
    account_match = _account_material(outcome["chain"]) == _account_material(frozen_corporate["gated_chain"])
    account_by_date = {row["date"]: row for row in outcome["chain"]["independent_account_checks"]}
    frozen_account_by_date = {row["date"]: row for row in
                              frozen_corporate["gated_chain"]["independent_account_checks"]}
    daily_account = [
        {"date": day, "public": account_by_date[day], "frozen_equal":
         account_by_date[day] == frozen_account_by_date.get(day)}
        for day in sorted(account_by_date)
    ]
    frozen_decisions = frozen_pilot["decision_trace"]
    daily_decisions = [
        {"symbol": symbol, **item,
         "frozen_equal": item == frozen_decisions[symbol][index] if index < len(frozen_decisions[symbol]) else False}
        for symbol in SYMBOLS for index, item in enumerate(outcome["decisions"][symbol])
    ]
    blockers = []
    if not decisions_match:
        blockers.append("PUBLIC_ENTRY_DAILY_DECISION_MISMATCH")
    if not rejections_match:
        blockers.append("PUBLIC_ENTRY_STATE_REJECTION_MISMATCH")
    if not account_match or not all(item["frozen_equal"] for item in daily_account):
        blockers.append("PUBLIC_ENTRY_ACCOUNT_MISMATCH")
    if outcome["chain"]["issues"]:
        blockers.append("PUBLIC_ENTRY_ACCOUNT_RECONCILIATION_FAILED")
    return {
        "purpose": "S1_FIXED_51_PUBLIC_STRATEGY_ENTRY_PARITY_DIAGNOSTIC",
        "public_entry": "chanlun_trader.research_factory.strategy_interface_v1.run",
        "strategy_id": strategy.strategy_id,
        "indicator_count": len(strategy.definition["indicators"]),
        "catalog_sha256": strategy.definition["catalog_sha256"],
        "scope": {"symbols": list(SYMBOLS), "feature_dates": [START_DATE, END_DATE],
                  "account_dates": [ACCOUNT_START_DATE, ACCOUNT_END_DATE]},
        "sources": source_hashes,
        "frozen_references": {"pilot_report_sha256": _file_hash(pilot_report_path),
                              "corporate_report_sha256": _file_hash(corporate_report_path)},
        "governance": governance_evidence,
        "decision_parity": decisions_match,
        "state_rejection_parity": rejections_match,
        "account_parity": account_match,
        "daily_decisions": daily_decisions,
        "daily_account": daily_account,
        "trade_count": outcome["chain"]["n_trades"],
        "order_count": outcome["chain"]["n_orders"],
        "no_trade_days": outcome["chain"]["n_no_trade_days"],
        "economic_result_sha256": _json_hash(_account_material(outcome["chain"])),
        "read_audit": read_audit,
        "public_entry_step_status": "PASSED_MODELED" if not blockers else "BLOCKED",
        "blockers": blockers,
        "s1_baseline_status": "NOT_PASSED",
        "remaining_s1_work": [
            "vendor turn and historical state publication times at the decision date",
            "dividend-adjusted indicator features versus raw execution prices",
            "full trading-engine checkpoint restart and independent replay",
            "real suspension and missing-data scenarios beyond synthetic negative tests",
        ],
        "strategy_effectiveness": "NOT_ASSESSED",
        "paper_eligibility": "NOT_ASSESSED",
    }


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
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"public_entry_step_status": result["public_entry_step_status"],
                      "blockers": result["blockers"], "report": str(REPORT_PATH)},
                     ensure_ascii=False))
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
