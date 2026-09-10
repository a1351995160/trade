"""R2 正式合成组合驱动；只接受新临时根，不加载 pytest 的替代 runner。"""
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime

import pandas as pd

from r2_formal_fixture import formal_fixture
from p3c_scenario import Scenario
from r1_fixture import write_json
from chanlun_trader.research_factory.structural_entry import StructuralEntryServiceV1
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.synthetic_novelty import SyntheticNoveltyBindingServiceV1
from chanlun_trader.research_factory.synthetic_novelty_start import SyntheticNoveltyTrialStartServiceV1
from chanlun_trader.research.pit_tradability import build_normalized_state
from chanlun_trader.research_factory.real_sample_feasibility import RealSampleFeasibilityProviderV1
from chanlun_trader.research_factory.sample_feasibility import CandidateSampleFeasibilityPreflightV1


def structural_proof(root, contract, policy):
    """从实际分片计算旧审计合同所需的证明，不能预填 PASS。"""
    inputs = RealSampleFeasibilityProviderV1(root, streaming=True, partition_session_count=80).build(
        contract.provider_candidate_payload(), policy)
    rows = [row for _, partition in inputs.observation_store.iter_partitions() for row in partition]
    assert rows
    assert all(isinstance(row["rank"], int) and row["rank"] >= 1 for row in rows)
    complete = [row for row in rows if row["holding_complete"] is True]
    assert complete
    assert all(row["holding_release_session_index"] is not None for row in complete)
    assert all(row["base_affordable"] is True and row["small_capital_affordable"] is True for row in complete)
    assert inputs.max_positions == contract.max_positions
    assert inputs.holding_horizon == contract.holding_period_trading_sessions
    result = CandidateSampleFeasibilityPreflightV1(policy).run(inputs).to_dict()
    assert result["lower_bound_count"] == result["portfolio_feasible_opportunity_count"]
    assert result["upper_bound_count"] >= result["lower_bound_count"] >= result["minimum_required_count"]
    write_json(root / "r2-structural-proof-measurements.json", {"row_count": len(rows),
        "complete_row_count": len(complete), "input_hash": result["input_hash"], "preflight": result})
    write_json(root / "reports/PLATFORM_ARCHITECTURE_GAP_ANALYSIS_V2.json", {
        "preflight_unknown_root_cause": {"proof": [
            "selection_unknown is false because ranks are materialized in the inspected synthetic partitions.",
            "capacity_unknown is false for the non-cross-sectional provider contract: cash, lot, slots and release sessions checked.",
            "END_OF_WINDOW_TRUNCATION is a frozen research-window tail boundary, not permission to invent completed exits.",
            "Therefore unknown potential rows expand the upper bound but do not erase the already accepted known subset from the lower-bound proof.",
        ], "evidence_ref": "r2-structural-proof-measurements.json"}})


def main():
    if os.environ.get("CHANLUN_TEST_ISOLATION") != "1":
        raise RuntimeError("IMPORT_TIME_ISOLATION_REQUIRED")
    root = Path(sys.argv[1]).resolve()
    if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()) or root.exists():
        raise RuntimeError("FRESH_TEMPORARY_ROOT_REQUIRED")
    root.mkdir()
    counts = {"structural_build": 0, "engine_run": 0, "predictive_execute": 0, "performance_access": 0,
              "synthetic_ai_design": 0, "external_ai_attempts": 0}

    def observe(frame, event, arg):
        if (event == "return" and frame.f_globals.get("__name__") == "chanlun_trader.research_daemon"
                and frame.f_code.co_name == "structural_preflight" and arg is not None):
            write_json(root / "r2-provider-result.json", {"status": arg.status, "reason_code": arg.reason_code, "details": dict(arg.details)})
        if event != "call":
            return
        module, name = frame.f_globals.get("__name__", ""), frame.f_code.co_name
        key = {
            ("chanlun_trader.research_factory.real_sample_feasibility", "build"): "structural_build",
            ("chanlun_trader.engine.engine", "run"): "engine_run",
            ("chanlun_trader.research_factory.predictive_executor", "execute"): "predictive_execute",
            ("chanlun_trader.research_factory.trial_adapter", "mark_performance_accessed"): "performance_access",
        }.get((module, name))
        if key:
            owner = frame.f_locals.get("self")
            if hasattr(owner, "root") and Path(owner.root).resolve() != root:
                raise AssertionError("SERVICE_ROOT_CONFLICT")
            counts[key] += 1
            if key == "engine_run" and len(sys.argv) > 2 and sys.argv[2] == "crash":
                write_json(root / "r2-test-counts.json", counts)
                print("R2_PROCESS_EXIT_AFTER_PERFORMANCE_BEFORE_ENGINE_BODY", flush=True)
                os._exit(73)
        if module.endswith("autonomous_orchestrator_v2") and name == "invoke":
            counts["external_ai_attempts"] += 1
            raise AssertionError("EXTERNAL_AI_DISABLED")
        if module == "p3c_scenario" and name == "backend":
            counts["synthetic_ai_design"] += 1

    sys.setprofile(observe)
    try:
        # 日期仅是预先声明的合成交易日历，不声称真实市场观察天数。
        sessions = [int(day.strftime("%Y%m%d")) for day in pd.bdate_range("2025-01-01", "2025-07-31")]
        caller, policy, contract, record, cache = formal_fixture(root, sessions)
        additional = None
        if len(sys.argv) > 2 and sys.argv[2] == "prepare_multiple":
            from r2_formal_fixture import additional_formal_candidate
            additional = additional_formal_candidate(root, sessions, holding_period=8)
        raw = root / "data/research/security_state/raw"
        codes = ["sz.000001", "sh.600000"]
        write_json(raw / "stock_basic.json", {"rows": [dict(code=code, type="1", ipoDate="2020-01-01", outDate="") for code in codes]})
        for code in codes:
            path = raw / "history" / ("symbol=" + code.replace(".", "_") + ".json")
            path.parent.mkdir(exist_ok=True)
            write_json(path, {"symbol": code, "rows": [dict(date=str(day), isST="0", tradestatus="1") for day in sessions]})
        for day in sessions:
            path = raw / "all_stock" / f"trade_date={day}.json"
            path.parent.mkdir(exist_ok=True)
            write_json(path, {"trade_date": str(day), "rows": [dict(code=code, tradeStatus="1") for code in codes]})
        build_normalized_state(raw, root / "data/research/security_state/normalized",
            datetime.strptime(str(sessions[0]), "%Y%m%d").date(), datetime.strptime(str(sessions[-1]), "%Y%m%d").date())
        structural_proof(root, contract, policy)
        if additional:
            structural_proof(root, additional[2], policy)
        # 本阶段安全证据取自已安装计数；它是结构检查资料，不是授权 receipt。
        write_json(root / "reports/PLATFORM_ARCHITECTURE_MANIFEST_V2.json", {"safety": {
            "outcome_blind": counts["performance_access"] == 0,
            "NEW_PREDICTIVE_TRIALS": counts["predictive_execute"], "PERFORMANCE_ACCESS": counts["performance_access"],
            "FINAL_TEST_ACCESS": {"analytical": 0, "decision": 0, "physical": 0}, "PROSPECTIVE": 0, "REAL_ORDER": "DISABLED"}})
        if len(sys.argv) > 2 and sys.argv[2] in {"prepare", "prepare_multiple"}:
            write_json(root / "r3-prepared-candidate.json", {"objective_id": caller.objective_id,
                "candidate_id": contract.candidate_id, "contract_hash": contract.content_hash})
            if additional:
                write_json(root / "r3-second-candidate.json", {"objective_id": additional[0].objective_id,
                    "candidate_id": additional[2].candidate_id, "contract_hash": additional[2].content_hash})
            return
        structural = StructuralEntryServiceV1(root).start(caller.objective_id, confirmed=True, candidate_id=contract.candidate_id)
        write_json(root / "r2-structural-result.json", structural)
        print("R2_STRUCTURAL=" + json.dumps(structural, ensure_ascii=False), flush=True)
        if structural.get("status") != "PASS":
            raise AssertionError("R2_REAL_STRUCTURAL_NOT_PASS")
        execution_policy = ExecutionPolicy("GOVERNED", "SYNTHETIC")
        novelty = SyntheticNoveltyBindingServiceV1(root)
        novelty.declare_sources(execution_policy, [f"data/research/research_factory/batches/{caller.objective_id}_B01/durable_frozen_candidate_contracts.json"])
        novelty_preview = novelty.preview(execution_policy, contract.candidate_id, contract.content_hash)
        novelty.confirm(execution_policy, {"confirmed": True, "preview_id": novelty_preview["preview_id"]})
        service = SyntheticNoveltyTrialStartServiceV1(root, execution_policy, novelty_preview["preview_id"], auto_run=False)
        if len(sys.argv) > 2 and sys.argv[2] == "prepare_legacy_intent":
            from chanlun_trader.research_factory.predictive_trial_start import PredictiveTrialStartServiceV1
            service = PredictiveTrialStartServiceV1(root, auto_run=False)
        before_authorization = service.readiness(caller.objective_id)
        assert before_authorization["available"] is False
        write_json(root / "r2-before-start-authorization.json", before_authorization)
        Scenario(root, caller.objective_id).authorize()
        preview = service.preview(caller.objective_id)
        request = {
            "confirmed": True, "action": "START_PREDICTIVE_TRIAL_1", "start_intent_id": "R2_SYNTHETIC_START",
            "candidate_id": contract.candidate_id, "candidate_hash": contract.candidate_hash,
            "preview_hash": preview["preview_hash"], "confirmation_token": preview["confirmation_token"],
        }
        write_json(root / "r2-test-start-request.json", request)
        receipt = service.confirm(caller.objective_id, request)
        write_json(root / "r2-start-receipt-copy.json", receipt)
        if len(sys.argv) > 2 and sys.argv[2] in {"prepare_intent", "prepare_legacy_intent"}:
            return
        service._run_intent(caller.objective_id, "R2_SYNTHETIC_START")
        result = service._load_intents(caller.objective_id)["R2_SYNTHETIC_START"]
        print("R2_FINAL=" + json.dumps(result, ensure_ascii=False), flush=True)
        write_json(root / "r2-test-result.json", {"intent": result, "counts": counts})
        assert result["stage"] == "TRIAL_COMPLETED", result
        batch = root / f"data/research/research_factory/batches/{caller.objective_id}_B01"
        ledger = json.loads((batch / "factory_trial_ledger.json").read_bytes())
        final = ledger["events"][-1]
        assert final["status"] == "COMPLETED" and final["performance_complete"] is True
        assert final["final_adjudicated"] is True and final["registry_committed"] is True
        budget = json.loads((batch / "search_budget_registry.json").read_bytes())
        assert all(bucket["used"] == 1 and bucket["reserved"] == 0 for bucket in budget["buckets"])
        strategies = json.loads((batch / "strategy_registry.json").read_bytes())["records"]
        assert len(strategies) == 1 and strategies[0]["promotion_state"] == "DISABLED"
        report = root / f"reports/research_daemon/{caller.objective_id}/predictive/{caller.objective_id}_B01/{contract.candidate_id}"
        failure = json.loads((report / "failure_extraction.json").read_bytes())
        assert failure["entries"]
        write_json(root / "r2-safe-summary.json", {"engineering_chain_complete": True, "classification": final["classification"],
            "strategy_state": strategies[0]["research_state"], "qualified_real_strategies": 0,
            "real_observation_days": 0, "failure_entries": len(failure["entries"]), "counts": counts})
    finally:
        sys.setprofile(None)
        write_json(root / "r2-test-counts.json", counts)
        print("R2_COUNTS=" + json.dumps(counts), flush=True)


if __name__ == "__main__":
    main()
