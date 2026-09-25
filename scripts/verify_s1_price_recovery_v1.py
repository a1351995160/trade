"""真实数据：历史可见性、因果特征/原始成交、中断重放三项联合验收。"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.engine import EngineConfig
from chanlun_trader.engine.signal import Side, Signal
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.degraded_execution_v2 import DegradedStateMasterV1
from chanlun_trader.research_factory.engine_replay_recovery_v1 import (
    ReplayInterrupted, economic_state, run_recoverable,
)
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.source_availability_v1 import s1_source_qualification
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run as public_run
from chanlun_trader.research_factory.common import stable_hash
from scripts.probe_all_indicator_strategy_v1 import (
    ACCOUNT_START_DATE, START_DATE, END_DATE, STRATEGY_ID, SYMBOLS,
    IndividualDividendPilotEngine,
)
from scripts.s1_causal_price_strategy_v1 import Causal51AccountBackend, Causal51VoteStrategy
from scripts.s1_public_entry_strategy_v1 import input_identity
from scripts.verify_s1_corporate_chain_v1 import ACCOUNT_END_DATE, ACTION_REPORT, checked_events
from scripts.verify_s1_public_entry_v1 import (
    CORPORATE_REPORT, PILOT_REPORT, REPORT_PATH as PUBLIC_REPORT,
    _account_material, _file_hash, _load_bundle,
)


REPORT_PATH = ROOT / "reports/s1_trusted_baseline_20260925/PRICE_PIT_RESTART.json"
REPLAY_SOURCE_FILES = (
    ROOT / "src/chanlun_trader/engine/engine.py",
    ROOT / "src/chanlun_trader/engine/corporate_action_engine_v1.py",
    ROOT / "src/chanlun_trader/engine/individual_dividend_accounting_v1.py",
    ROOT / "src/chanlun_trader/research_factory/degraded_execution_v2.py",
    ROOT / "src/chanlun_trader/research_factory/engine_replay_recovery_v1.py",
    ROOT / "scripts/probe_all_indicator_strategy_v1.py",
    ROOT / "scripts/s1_public_entry_strategy_v1.py",
    ROOT / "scripts/s1_causal_price_strategy_v1.py",
    Path(__file__).resolve(),
)


def _replay_identity(input_identity: str) -> str:
    return stable_hash({"input_identity": input_identity,
                        "code_sha256": {str(path.relative_to(ROOT)): _file_hash(path)
                                        for path in REPLAY_SOURCE_FILES}})


def _engine(bundle: dict, decisions: dict, events: tuple[dict, ...], definition: dict):
    daily, states = bundle["daily"], bundle["states"]
    days = sorted(int(day) for day in daily.loc[
        daily.date.between(ACCOUNT_START_DATE, ACCOUNT_END_DATE), "date"].unique())
    if days[0] != ACCOUNT_START_DATE or days[-1] != ACCOUNT_END_DATE:
        raise ValueError("REPLAY_ACCOUNT_CALENDAR_INCOMPLETE")
    store = MarketDataStore()
    for symbol in SYMBOLS:
        bars = daily.loc[daily.symbol == symbol].sort_values("date")
        if set(days) - set(bars.date.astype(int)):
            raise ValueError("REPLAY_DAILY_COVERAGE_INCOMPLETE")
        store.add_daily_raw(symbol, bars.set_index("date")[[
            "open", "high", "low", "close", "volume", "amount", "prev_close"]])
    hashes = bundle["source_hashes"]
    source_hash = hashlib.sha256((
        f"{hashes['daily_sha256']}:{hashes['turn_sha256']}:{hashes['states_sha256']}:"
        f"{hashes['corporate_actions_sha256']}:{hashes['historical_states_sha256']}"
    ).encode()).hexdigest()
    config = EngineConfig(
        initial_cash=1_000_000.0, max_positions=2, max_position_weight=0.5,
        max_holding_days=1000, mode="DAILY", feature_price_mode="raw",
        start_date=days[0], end_date=days[-1], enable_index_filter=False,
        index_filter_enabled=False, strategy_hash=definition["catalog_sha256"],
        data_manifest_hash=source_hash,
        execution_model_version="ALL_51_S1_INDIVIDUAL_DIVIDEND_V1",
        persist_run_manifest=False,
    )
    dataset = SimpleNamespace(dataset_id="ALL_51_S1_PERSONAL_CASH_DIVIDENDS_V1",
                              events=events, coverage_symbols=SYMBOLS,
                              manifest_sha256=hashes["corporate_actions_sha256"])
    engine = IndividualDividendPilotEngine(
        store, days, config=config, seed=0,
        security_master=DegradedStateMasterV1(states), action_dataset=dataset)
    signals = []
    for symbol in SYMBOLS:
        for item in decisions[symbol]:
            day = item["date"]
            if day not in days:
                continue
            timestamp = pd.Timestamp(str(day), tz="Asia/Shanghai") + pd.Timedelta(hours=15, minutes=30)
            signals.append(Signal(
                strategy_id=STRATEGY_ID, signal_id=f"{STRATEGY_ID}:{symbol}:{day}",
                symbol=symbol, generated_at=timestamp,
                direction=Side(item["decision_at_close"]),
                metadata={"rising_votes": item["rising_votes"]},
            ))
    engine.add_signals(signals)
    return engine


def _engine_chain_parity(result, chain: dict) -> bool:
    trades = sorted(result.trades, key=lambda trade: (trade.fill_time, trade.trade_id))
    projection = [{"id": trade.trade_id, "order_id": trade.order_id,
                   "symbol": trade.symbol, "side": trade.side.value,
                   "date": int(trade.fill_time.strftime("%Y%m%d")),
                   "quantity": trade.quantity, "price": trade.price,
                   "gross_value": trade.gross_value, "fee": trade.fee,
                   "reality_flag": trade.reality_flag} for trade in trades]
    return (projection == chain["trades"]
            and len(result.orders.orders) == chain["n_orders"]
            and result.ledger.action_income == chain["corporate_account"]["dividend_income"]
            and result.ledger.dividend_tax_withheld == chain["corporate_account"]["dividend_tax_withheld"])


def _restart_worker(package_path: Path, phase: str) -> int:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    paths = {key: Path(value) for key, value in package["paths"].items()}
    hashes = package["source_hashes"]
    for key, path in paths.items():
        if _file_hash(path) != hashes[key + "_sha256"]:
            raise ValueError(f"RESTART_WORKER_SOURCE_CHANGED:{key}")
    strategy = Causal51VoteStrategy()
    events = tuple(package["events"])
    bundle = _load_bundle(paths, hashes, [], events, strategy)
    if bundle["input_identity"] != package["input_identity"]:
        raise ValueError("RESTART_WORKER_INPUT_IDENTITY_CHANGED")
    if _replay_identity(bundle["input_identity"]) != package["replay_identity"]:
        raise ValueError("RESTART_WORKER_CODE_CHANGED")
    if stable_hash(package["decisions"]) != package["decisions_sha256"]:
        raise ValueError("RESTART_WORKER_DECISIONS_CHANGED")
    engine = _engine(bundle, package["decisions"], events, strategy.definition)
    checkpoint = Path(package["checkpoint_path"])
    if phase == "interrupt":
        try:
            run_recoverable(engine, checkpoint, input_identity=package["replay_identity"],
                            stop_after_date=package["interrupt_after_date"])
        except ReplayInterrupted:
            os._exit(17)
        raise ValueError("RESTART_WORKER_INTERRUPTION_NOT_REACHED")
    if phase != "resume":
        raise ValueError("RESTART_WORKER_PHASE_INVALID")
    result = run_recoverable(engine, checkpoint, input_identity=package["replay_identity"])
    Path(package["result_path"]).write_text(json.dumps({
        "economic_result_sha256": stable_hash(economic_state(engine)),
        "trade_count": len(result.trades),
        "dividend_income": result.ledger.action_income,
        "dividend_tax_withheld": result.ledger.dividend_tax_withheld,
    }), encoding="utf-8")
    return 0


def run(daily_path: Path, turn_path: Path, states_path: Path,
        historical_path: Path, turn_manifest_path: Path,
        action_path: Path = ACTION_REPORT) -> dict:
    frozen = json.loads(PUBLIC_REPORT.read_text(encoding="utf-8"))
    corporate = json.loads(CORPORATE_REPORT.read_text(encoding="utf-8"))
    pilot = json.loads(PILOT_REPORT.read_text(encoding="utf-8"))
    events = checked_events(json.loads(action_path.read_text(encoding="utf-8")))
    paths = {"daily": daily_path, "turn": turn_path, "states": states_path,
             "historical_states": historical_path, "turn_manifest": turn_manifest_path,
             "corporate_actions": action_path}
    hashes = {"daily_sha256": _file_hash(daily_path),
              "turn_sha256": _file_hash(turn_path),
              "states_sha256": _file_hash(states_path),
              "historical_states_sha256": _file_hash(historical_path),
              "actions_sha256": corporate["sources"]["actions_sha256"],
              "corporate_actions_sha256": _file_hash(action_path),
              "turn_manifest_sha256": _file_hash(turn_manifest_path)}
    if (hashes != corporate["sources"] or not frozen["decision_parity"]
            or not frozen["account_parity"] or pilot["strategy"]["catalog_sha256"]
            != frozen["catalog_sha256"]):
        raise ValueError("FROZEN_S1_INPUT_OR_REFERENCE_CHANGED")
    strategy, backend = Causal51VoteStrategy(), Causal51AccountBackend(events)
    read_audit = []
    bundle = _load_bundle(paths, hashes, read_audit, events, strategy)
    if input_identity(bundle, events, strategy.definition["catalog_sha256"]) != bundle["input_identity"]:
        raise ValueError("S1_INPUT_IDENTITY_CONFLICT")
    replay_identity = _replay_identity(bundle["input_identity"])
    plan = prepare(strategy, backend)
    objective = "S1_CAUSAL_PRICE_AND_RESTART_DIAGNOSTIC"
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="s1_price_recovery_") as temporary:
        temp = Path(temporary)
        budget_path = temp / "BUDGET.json"
        budget = SearchBudgetRegistryV1(objective, budget_path)
        budget.register_objective(1)
        budget.consume(budget.reserve("objective", objective))
        governance = StrategyBatchGovernanceV1(
            temp / "GOVERNANCE", budget_path, objective, {strategy.strategy_id: plan})
        governance.confirm(
            {"origin": "USER_EXPLICIT_CURRENT_TASK",
             "statement": "固定51指标三项联合验收：来源时间、除息价格与中断恢复",
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
            outcome = public_run(strategy, backend, frame=bundle, actions=events,
                                 input_identity=bundle["input_identity"],
                                 active_check=lambda: governance.active_execution(strategy.strategy_id))
        finally:
            governance.settle(strategy.strategy_id, completed=outcome is not None,
                              seconds=round(time.perf_counter() - started, 3),
                              result_hash=stable_hash(_account_material(outcome["chain"])) if outcome else "NO_RESULT",
                              error=None if outcome else "CAUSAL_PRICE_EXECUTION_FAILED")
        qualification = s1_source_qualification(
            json.loads(turn_manifest_path.read_text(encoding="utf-8")),
            bundle["daily"],
            bundle["turn"],
            bundle["historical"], account_end_date=ACCOUNT_END_DATE)
        direct = _engine(bundle, outcome["submitted_decisions"], events, strategy.definition)
        direct_result = direct.run()
        direct_hash = stable_hash(economic_state(direct))
        parity = _engine_chain_parity(direct_result, outcome["chain"])
        restarts = []
        for day in (20240717, 20240718):
            checkpoint = temp / f"CHECKPOINT_{day}.json"
            try:
                run_recoverable(_engine(bundle, outcome["submitted_decisions"], events,
                                        strategy.definition), checkpoint,
                                input_identity=replay_identity, stop_after_date=day)
            except ReplayInterrupted:
                pass
            else:
                raise ValueError("REPLAY_INTERRUPTION_NOT_REACHED")
            recorded = json.loads(checkpoint.read_text(encoding="utf-8"))
            resumed = _engine(bundle, outcome["submitted_decisions"], events, strategy.definition)
            resumed_result = run_recoverable(
                resumed, checkpoint, input_identity=replay_identity)
            resumed_hash = stable_hash(economic_state(resumed))
            restarts.append({"interrupted_after_date": day,
                             "checkpoint_event_index": recorded["event_index"],
                             "checkpoint_state_sha256": recorded["state_hash"],
                             "prefix_replay_verified": True,
                             "economic_result_equal": resumed_hash == direct_hash,
                             "chain_trade_dividend_equal": _engine_chain_parity(resumed_result, outcome["chain"])})
        checkpoint = temp / "PROCESS_CHECKPOINT_20240718.json"
        result_path = temp / "PROCESS_RESULT.json"
        package_path = temp / "PROCESS_PACKAGE.json"
        package = {
            "paths": {"daily": str(daily_path), "turn": str(turn_path),
                      "states": str(states_path),
                      "historical_states": str(historical_path),
                      "turn_manifest": str(turn_manifest_path),
                      "corporate_actions": str(action_path)},
            "source_hashes": hashes, "events": events,
            "decisions": outcome["submitted_decisions"],
            "decisions_sha256": stable_hash(outcome["submitted_decisions"]),
            "input_identity": bundle["input_identity"],
            "replay_identity": replay_identity,
            "interrupt_after_date": 20240718,
            "checkpoint_path": str(checkpoint), "result_path": str(result_path),
        }
        package_path.write_text(json.dumps(package, ensure_ascii=False, default=str),
                                encoding="utf-8")
        command = [sys.executable, str(Path(__file__).resolve()),
                   "--restart-worker-package", str(package_path)]
        interrupted = subprocess.run(command + ["--restart-worker-phase", "interrupt"],
                                     cwd=ROOT, capture_output=True, text=True, timeout=180)
        if interrupted.returncode != 17 or not checkpoint.exists():
            raise ValueError("REAL_DATA_PROCESS_INTERRUPTION_FAILED:" + interrupted.stderr[-1000:])
        resumed = subprocess.run(command + ["--restart-worker-phase", "resume"],
                                 cwd=ROOT, capture_output=True, text=True, timeout=180)
        if resumed.returncode != 0 or not result_path.exists():
            raise ValueError("REAL_DATA_PROCESS_RESUME_FAILED:" + resumed.stderr[-1000:])
        process_result = json.loads(result_path.read_text(encoding="utf-8"))
        cross_process = {"interrupted_after_date": 20240718,
                         "exit_code": interrupted.returncode,
                         "separate_process_resume": True,
                         "economic_result_equal": process_result["economic_result_sha256"] == direct_hash,
                         "trade_count_equal": process_result["trade_count"] == outcome["chain"]["n_trades"],
                         "dividend_income_equal": process_result["dividend_income"] ==
                         outcome["chain"]["corporate_account"]["dividend_income"],
                         "dividend_tax_equal": process_result["dividend_tax_withheld"] ==
                         outcome["chain"]["corporate_account"]["dividend_tax_withheld"]}
    frozen_decisions = {row["symbol"]: [] for row in frozen["daily_decisions"]}
    for row in frozen["daily_decisions"]:
        frozen_decisions[row["symbol"]].append(row)
    changed = {symbol: sum(a["decision_at_close"] != b["decision_at_close"]
                            for a, b in zip(outcome["decisions"][symbol], frozen_decisions[symbol]))
               for symbol in SYMBOLS}
    blockers = []
    if outcome["chain"]["issues"] or not parity or not all(
            row["economic_result_equal"] and row["chain_trade_dividend_equal"] for row in restarts):
        blockers.append("CAUSAL_ACCOUNT_OR_RESTART_MISMATCH")
    if not all(value for key, value in cross_process.items() if key.endswith("_equal")):
        blockers.append("CROSS_PROCESS_RESTART_MISMATCH")
    if sum(len(rows) for rows in backend.price_evidence.values()) != len(events):
        blockers.append("DIVIDEND_FEATURE_EVIDENCE_INCOMPLETE")
    return {"purpose": "S1_FIXED_51_PIT_PRICE_RESTART_DIAGNOSTIC",
            "status": "PASSED_MODELED" if not blockers else "BLOCKED",
            "s1_baseline_status": "NOT_PASSED",
            "strict_source_qualification": qualification,
            "source_hashes": hashes, "public_reference_sha256": _file_hash(PUBLIC_REPORT),
            "strategy_plan_id": plan["plan_id"], "input_identity": bundle["input_identity"],
            "replay_identity": replay_identity,
            "feature_mode": "CAUSAL_HFQ_CASH_DIVIDEND_ONLY",
            "execution_and_valuation_mode": "RAW",
            "price_evidence": backend.price_evidence,
            "decision_changes_vs_raw_features": changed,
            "account_dates": outcome["chain"]["account_dates"],
            "account_days": outcome["chain"]["n_account_days"],
            "trade_count": outcome["chain"]["n_trades"],
            "order_count": outcome["chain"]["n_orders"],
            "independent_account_issues": outcome["chain"]["issues"],
            "account_reconciliation_sha256": stable_hash(_account_material(outcome["chain"])),
            "continuous_engine_sha256": direct_hash,
            "continuous_matches_public_chain": parity,
            "restart_checks": restarts, "cross_process_restart": cross_process,
            "read_audit": read_audit,
            "blockers": blockers,
            "remaining_s1_work": qualification["blockers"] + [
                "REAL_SUSPENSION_AND_MISSING_DATA_SCENARIOS_NOT_OBSERVED"],
            "strategy_effectiveness": "NOT_ASSESSED", "paper_eligibility": "BLOCKED"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-parquet", type=Path, required=True)
    parser.add_argument("--turn-parquet", type=Path, required=True)
    parser.add_argument("--states-parquet", type=Path, required=True)
    parser.add_argument("--historical-states-parquet", type=Path, required=True)
    parser.add_argument("--turn-manifest-json", type=Path, required=True)
    parser.add_argument("--action-snapshot-json", type=Path, default=ACTION_REPORT)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve() != REPORT_PATH.resolve():
        parser.error(f"--report must be {REPORT_PATH}")
    result = run(args.daily_parquet, args.turn_parquet, args.states_parquet,
                 args.historical_states_parquet, args.turn_manifest_json,
                 args.action_snapshot_json)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": result["status"], "s1_baseline_status": result["s1_baseline_status"],
                      "source_qualification": result["strict_source_qualification"]["strict_pit_status"],
                      "blockers": result["blockers"], "report": str(REPORT_PATH)},
                     ensure_ascii=False))
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    if "--restart-worker-package" in sys.argv:
        worker_parser = argparse.ArgumentParser()
        worker_parser.add_argument("--restart-worker-package", type=Path, required=True)
        worker_parser.add_argument("--restart-worker-phase", choices=("interrupt", "resume"), required=True)
        worker_args = worker_parser.parse_args()
        raise SystemExit(_restart_worker(worker_args.restart_worker_package,
                                         worker_args.restart_worker_phase))
    raise SystemExit(main())
