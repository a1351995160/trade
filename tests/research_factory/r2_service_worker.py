"""R2 探索驱动；当前旧 fixture 缺正式目标绑定/家族，不能作为完整验收通过。

只接受全新临时根，使用 import-time isolation，不加载 pytest conftest。
完整路径在正式目标创建协议批准和实现之前保持阻塞。
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime

import pandas as pd

from r1_caller_fixture import fixture
from p3c_scenario import Scenario
from r1_fixture import write_json
from chanlun_trader.research_factory.structural_entry import StructuralEntryServiceV1
from chanlun_trader.research_factory.predictive_trial_start import PredictiveTrialStartServiceV1
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
        if module.endswith("autonomous_orchestrator_v2") and name == "invoke":
            counts["external_ai_attempts"] += 1
            raise AssertionError("EXTERNAL_AI_DISABLED")
        if module == "p3c_scenario" and name == "backend":
            counts["synthetic_ai_design"] += 1

    sys.setprofile(observe)
    try:
        # 日期仅是预先声明的合成交易日历，不声称真实市场观察天数。
        sessions = [int(day.strftime("%Y%m%d")) for day in pd.bdate_range("2025-01-01", "2025-07-31")]
        caller, policy, contract, record, cache = fixture(root, sessions=sessions, validation_ready=True)
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
        # 本阶段安全证据取自已安装计数；它是结构检查资料，不是授权 receipt。
        write_json(root / "reports/PLATFORM_ARCHITECTURE_MANIFEST_V2.json", {"safety": {
            "outcome_blind": counts["performance_access"] == 0,
            "NEW_PREDICTIVE_TRIALS": counts["predictive_execute"], "PERFORMANCE_ACCESS": counts["performance_access"],
            "FINAL_TEST_ACCESS": {"analytical": 0, "decision": 0, "physical": 0}, "PROSPECTIVE": 0, "REAL_ORDER": "DISABLED"}})
        structural = StructuralEntryServiceV1(root).start(caller.objective_id, confirmed=True, candidate_id=contract.candidate_id)
        write_json(root / "r2-structural-result.json", structural)
        print("R2_STRUCTURAL=" + json.dumps(structural, ensure_ascii=False), flush=True)
        if structural.get("status") != "PASS":
            raise AssertionError("R2_REAL_STRUCTURAL_NOT_PASS")
        Scenario(root).authorize()
        service = PredictiveTrialStartServiceV1(root, auto_run=False)
        preview = service.preview(caller.objective_id)
        receipt = service.confirm(caller.objective_id, {
            "confirmed": True, "action": "START_PREDICTIVE_TRIAL_1", "start_intent_id": "R2_SYNTHETIC_START",
            "candidate_id": contract.candidate_id, "candidate_hash": contract.candidate_hash,
            "preview_hash": preview["preview_hash"], "confirmation_token": preview["confirmation_token"],
        })
        write_json(root / "r2-start-receipt-copy.json", receipt)
        service._run_intent(caller.objective_id, "R2_SYNTHETIC_START")
        result = service._load_intents(caller.objective_id)["R2_SYNTHETIC_START"]
        print("R2_FINAL=" + json.dumps(result, ensure_ascii=False), flush=True)
        write_json(root / "r2-test-result.json", {"intent": result, "counts": counts})
        assert result["stage"] == "TRIAL_COMPLETED", result
    finally:
        sys.setprofile(None)
        write_json(root / "r2-test-counts.json", counts)
        print("R2_COUNTS=" + json.dumps(counts), flush=True)


if __name__ == "__main__":
    main()
