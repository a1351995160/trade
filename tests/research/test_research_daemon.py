from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from chanlun_trader.research_daemon import (
    CandidateWork,
    CanonicalResearchRuntime,
    PredictiveResult,
    ResourceMonitorV1,
    ResearchDaemon,
    STRUCTURAL_SAFETY_STATISTIC_ALLOWED_PATHS,
    StructuralResult,
    SyntheticResearchDaemonRuntime,
)
from chanlun_trader.research_factory.context import PerformanceBlindGuard, PerformanceLeakError
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.contract_correction import FrozenContractCorrectionServiceV1
from chanlun_trader.research_factory.objective import ResearchObjectiveV1
from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_daemon_state import (
    DaemonAlreadyRunningError,
    DaemonCheckpointStoreV1,
    DaemonInstanceLockV1,
    ResearchDaemonState,
)


def candidate(name: str) -> CandidateWork:
    return CandidateWork(name, f"hash-{name}", f"contracts/{name}.json", "SYNTHETIC_BATCH", name.lower())


def test_memory_pressure_before_structural_run_enters_resource_wait_safely(tmp_path: Path) -> None:
    runtime = SyntheticResearchDaemonRuntime(
        [candidate("MEMORY")],
        structural={"MEMORY": StructuralResult("PASS", "MUST_NOT_RUN_WHILE_MEMORY_PRESSURED")},
        budget_total=1,
    )
    daemon = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)
    daemon.monitor.snapshot = lambda *, stage, **_: {
        "memory_pressure": stage == ResearchDaemonState.STRUCTURAL_PENDING.value,
    }

    status = daemon.run_once()

    assert status["daemon_state"] == ResearchDaemonState.RESOURCE_WAIT.value
    assert status["required_human_ai_action"] == "RESOURCE_WAIT"
    assert runtime.structural_calls == []
    checkpoint = DaemonCheckpointStoreV1(tmp_path, daemon.objective_id).load()
    assert checkpoint.current_state == ResearchDaemonState.RESOURCE_WAIT.value
    assert checkpoint.current_trial is None


def test_long_run_sequence_preserves_candidate_identity_and_predictive_exact_once(tmp_path: Path) -> None:
    runtime = SyntheticResearchDaemonRuntime(
        [candidate("A"), candidate("B"), candidate("C")],
        structural={
            "A": StructuralResult("BLOCKED", "STRUCTURAL_BLOCKED_FIXTURE"),
            "B": StructuralResult("PASS", "STRUCTURAL_PASS_FIXTURE"),
            "C": StructuralResult("ENGINEERING_BLOCKED", "FROZEN_CONTRACT_IDENTITY_CONFLICT"),
        },
        predictive={"B": PredictiveResult("TRIAL_B", "COMPLETED", True, "REJECTED")},
        budget_total=2,
    )
    daemon = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)

    assert daemon.run_once()["daemon_state"] == ResearchDaemonState.READY.value
    assert daemon.run_once()["daemon_state"] == ResearchDaemonState.READY.value
    assert daemon.run_once()["daemon_state"] == ResearchDaemonState.ENGINEERING_BLOCKED.value
    assert runtime.structural_calls == ["A", "B", "C"]
    assert runtime.predictive_calls == ["B"]
    assert runtime.completed == {"A", "B"}
    assert daemon.status_payload()["budget"]["used"] == 1
    assert daemon.checkpoint is not None
    assert daemon.checkpoint.current_candidate["candidate_id"] == "C"
    assert daemon.checkpoint.retry_safe is True


def test_no_candidates_enters_human_triggered_no_outcome_handoff(tmp_path: Path) -> None:
    runtime = SyntheticResearchDaemonRuntime([], budget_total=12, budget_used=3, global_search_exhausted=False)
    daemon = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)

    status = daemon.run_once()

    assert status["daemon_state"] == ResearchDaemonState.NEED_AI_RESEARCH_DESIGN.value
    handoff = json.loads((tmp_path / "reports/RESEARCH_DAEMON_HANDOFF_CURRENT.json").read_text(encoding="utf-8"))
    assert handoff["handoff_type"] == "NEED_AI_RESEARCH_DESIGN"
    assert handoff["ai_auto_invocation"] == "DISABLED"
    assert handoff["performance_values_exposed"] is False
    serialized = json.dumps(handoff, ensure_ascii=False).casefold()
    for forbidden in ("exact_return", "profit_factor", "drawdown", "win_rate", "p_value"):
        assert forbidden not in serialized


def test_canonical_runtime_rehydrates_structural_completion_from_daemon_events(tmp_path: Path) -> None:
    objective_id = "OBJECTIVE_DAEMON_STRUCTURAL_REHYDRATION"
    objective_path = tmp_path / "data/research/research_factory/objectives" / f"{objective_id}.json"
    objective_path.parent.mkdir(parents=True)
    objective_path.write_text(json.dumps(ResearchObjectiveV1.default(objective_id).to_dict()), encoding="utf-8")

    budget_path = tmp_path / "data/research/research_factory/batches/B01/search_budget_registry.json"
    registry = SearchBudgetRegistryV1(objective_id, budget_path)
    registry.register_objective(4)
    contract_path = budget_path.parent / "durable_frozen_candidate_contracts.json"
    contract_path.write_text(json.dumps({"contracts": [{"candidate_id": "CANDIDATE_001", "candidate_hash": "HASH_001", "policy_identity": {"objective_id": objective_id}, "mechanism": "event_reversal"}]}), encoding="utf-8")

    events_path = tmp_path / "reports/research_daemon" / objective_id / "daemon_events.jsonl"
    events_path.parent.mkdir(parents=True)
    events = [
        {"candidate": "CANDIDATE_001", "new_state": "STRUCTURAL_PENDING"},
        {"candidate": "CANDIDATE_001", "new_state": "STRUCTURAL_UNKNOWN"},
        {"candidate": None, "new_state": "CANDIDATE_COMPLETE"},
    ]
    events_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    runtime = CanonicalResearchRuntime(tmp_path, objective_id=objective_id)

    assert runtime.next_candidate() is None


def test_canonical_runtime_excludes_append_only_invalidated_contract(tmp_path: Path) -> None:
    objective_id = "OBJECTIVE_DAEMON_CONTRACT_INVALIDATION"
    objective_path = tmp_path / "data/research/research_factory/objectives" / f"{objective_id}.json"
    objective_path.parent.mkdir(parents=True)
    objective_path.write_text(json.dumps(ResearchObjectiveV1.default(objective_id).to_dict()), encoding="utf-8")
    budget_path = tmp_path / "data/research/research_factory/batches/B01/search_budget_registry.json"
    budget = SearchBudgetRegistryV1(objective_id, budget_path)
    budget.register_objective(4)
    contract_path = budget_path.parent / "durable_frozen_candidate_contracts.json"
    contract_path.write_text(json.dumps({"contracts": [{"candidate_id": "CANDIDATE_INVALID", "candidate_hash": "HASH_INVALID", "policy_identity": {"objective_id": objective_id}, "mechanism": "event_reversal"}]}), encoding="utf-8")
    FrozenContractCorrectionServiceV1(tmp_path).invalidate_incompatible_contract(
        objective_id=objective_id,
        candidate_id="CANDIDATE_INVALID",
        expected_candidate_hash="HASH_INVALID",
        contract_ref=contract_path.relative_to(tmp_path).as_posix(),
    )

    runtime = CanonicalResearchRuntime(tmp_path, objective_id=objective_id)

    assert runtime.next_candidate() is None
    assert runtime.summary()["remaining_frozen_candidates"] == 0
    assert runtime._engineering_blocked["CANDIDATE_INVALID"] == "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE"


def test_pause_request_is_honored_at_safe_boundary(tmp_path: Path) -> None:
    runtime = SyntheticResearchDaemonRuntime([candidate("A")], structural={"A": StructuralResult("PASS")}, budget_total=1)
    daemon = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)
    daemon.store.request("PAUSE")

    status = daemon.run_once()

    assert status["daemon_state"] == ResearchDaemonState.PAUSED.value
    assert runtime.structural_calls == []


def test_governed_new_mechanism_structural_pass_requires_human_predictive_authorization(tmp_path: Path) -> None:
    """A governed first PASS must never fall through into predictive access."""

    current = candidate("GOVERNED")
    objective_id = "SYNTHETIC_OBJECTIVE"
    objective_path = tmp_path / "data/research/research_factory/objectives" / f"{objective_id}.json"
    objective_path.parent.mkdir(parents=True)
    objective_path.write_text(
        json.dumps(
            {
                "objective_id": objective_id,
                "governance_action": "START_NEW_MECHANISM_OBJECTIVE",
            }
        ),
        encoding="utf-8",
    )
    structural = StructuralResult(
        "PASS",
        "B_LOWER_BOUND_MEETS_MINIMUM",
        details={
            "v1_result": {
                "candidate_id": current.candidate_id,
                "candidate_hash": current.candidate_hash,
                "outcome_blind": True,
                "performance_data_loaded": False,
                "lower_bound_count": 35,
                "upper_bound_count": 35,
                "minimum_required_count": 30,
            },
            "lower_bound_integrity": {"status": "PASS", "failure_codes": []},
        },
    )
    runtime = SyntheticResearchDaemonRuntime(
        [current],
        structural={current.candidate_id: structural},
        predictive={
            current.candidate_id: PredictiveResult(
                "MUST_NOT_RUN",
                "COMPLETED",
                True,
                "REJECTED",
            )
        },
        budget_total=6,
    )
    daemon = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)

    status = daemon.run_once()

    assert status["daemon_state"] == ResearchDaemonState.STRUCTURAL_PASS.value
    assert status["budget"] == {"used": 0, "total": 6, "remaining": 6, "reserved": 0}
    assert runtime.structural_calls == [current.candidate_id]
    assert runtime.predictive_calls == []
    assert runtime.trials == {}
    assert daemon.checkpoint is not None
    assert daemon.checkpoint.required_action == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert daemon.checkpoint.current_candidate is None
    assert daemon.checkpoint.current_trial is None
    assert daemon.checkpoint.last_completed_candidate["candidate_id"] == current.candidate_id
    reconciliation = dict(daemon.checkpoint.canonical_refs.get("structural_reconciliation") or {})
    assert reconciliation["status"] == "PASS"
    assert reconciliation["predictive_run_started"] is False

    governance_path = (
        tmp_path
        / "reports/research_orchestrator_v2"
        / objective_id
        / "structural_governance_decision_required.json"
    )
    governance = json.loads(governance_path.read_text(encoding="utf-8"))
    assert governance["decision_mode"] == "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED"
    assert governance["status"] == "PENDING_HUMAN_DECISION"
    assert governance["candidate_id"] == current.candidate_id
    assert governance["candidate_hash"] == current.candidate_hash
    assert governance["structural"]["status"] == "PASS"
    assert governance["structural"]["lower_bound"] == 35
    assert governance["predictive_trials_created"] == 0
    assert governance["performance_access"] == 0

    restarted = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)
    restarted_status = restarted.run_once()
    assert restarted_status["daemon_state"] == ResearchDaemonState.STRUCTURAL_PASS.value
    assert restarted.checkpoint is not None
    assert restarted.checkpoint.required_action == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert runtime.structural_calls == [current.candidate_id]
    assert runtime.predictive_calls == []
    assert runtime.trials == {}
    assert restarted_status["budget"] == {"used": 0, "total": 6, "remaining": 6, "reserved": 0}


def test_budget_exhaustion_before_candidate_selection_is_terminal(tmp_path: Path) -> None:
    runtime = SyntheticResearchDaemonRuntime(
        [candidate("A")],
        structural={"A": StructuralResult("PASS", "STRUCTURAL_PASS_FIXTURE")},
        budget_total=1,
        budget_used=1,
    )
    daemon = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)

    status = daemon.run_once()

    assert status["daemon_state"] == ResearchDaemonState.BUDGET_EXHAUSTED.value
    assert status["budget"]["used"] == 1
    assert status["budget"]["remaining"] == 0
    assert runtime.predictive_calls == []
    assert daemon.checkpoint is not None
    assert daemon.checkpoint.last_error is None


def test_restart_from_structural_pass_with_exhausted_budget_is_terminal(tmp_path: Path) -> None:
    runtime = SyntheticResearchDaemonRuntime(
        [candidate("A")],
        structural={"A": StructuralResult("PASS", "STRUCTURAL_PASS_FIXTURE")},
        budget_total=1,
        budget_used=1,
    )
    daemon = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)
    daemon._ensure_bootstrap()
    assert daemon.checkpoint is not None
    daemon.checkpoint = daemon.checkpoint.update(current_candidate=runtime.candidates[0].to_dict())
    daemon._transition(ResearchDaemonState.STRUCTURAL_PENDING, "FIXTURE_CANDIDATE_SELECTED")
    daemon._transition(ResearchDaemonState.STRUCTURAL_RUNNING, "FIXTURE_STRUCTURAL_STARTED")
    daemon._transition(ResearchDaemonState.STRUCTURAL_PASS, "STRUCTURAL_PASS_FIXTURE")

    restarted = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)
    status = restarted.run_once()

    assert status["daemon_state"] == ResearchDaemonState.BUDGET_EXHAUSTED.value
    assert status["budget"]["remaining"] == 0
    assert runtime.structural_calls == []
    assert runtime.predictive_calls == []
    assert restarted.checkpoint is not None
    assert restarted.checkpoint.last_error is None


def test_crash_after_predictive_access_cannot_get_free_retry(tmp_path: Path) -> None:
    class CrashAfterAccessRuntime(SyntheticResearchDaemonRuntime):
        def predictive_validate(self, current: CandidateWork) -> PredictiveResult:
            self.predictive_calls.append(current.candidate_id)
            result = PredictiveResult("TRIAL_CRASHED", "PERFORMANCE_ACCESSED", True, None)
            self.trials[current.candidate_id] = result
            raise KeyboardInterrupt("synthetic crash after PerformanceAccessGate")

    runtime = CrashAfterAccessRuntime([candidate("A")], structural={"A": StructuralResult("PASS")}, budget_total=1)
    daemon = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)
    with pytest.raises(KeyboardInterrupt):
        daemon.run_once()

    restarted = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)
    status = restarted.run_once()
    assert status["daemon_state"] == ResearchDaemonState.ENGINEERING_BLOCKED.value
    assert runtime.predictive_calls == ["A"]
    assert restarted.checkpoint is not None
    assert restarted.checkpoint.retry_safe is False


def test_single_instance_and_stale_lock_recovery(tmp_path: Path) -> None:
    store = DaemonCheckpointStoreV1(tmp_path, "OBJ")
    first = DaemonInstanceLockV1(store.lock_path, "OBJ")
    first.acquire(run_id="RUN1")
    second = DaemonInstanceLockV1(store.lock_path, "OBJ")
    with pytest.raises(DaemonAlreadyRunningError):
        second.acquire(run_id="RUN2")
    first.release()

    store.lock_path.write_text(json.dumps({"pid": 99999999, "objective_id": "OBJ", "owner_id": "stale"}), encoding="utf-8")
    recovered = DaemonInstanceLockV1(store.lock_path, "OBJ")
    recovered.acquire(run_id="RUN3")
    assert json.loads(store.lock_path.read_text(encoding="utf-8"))["daemon_run_id"] == "RUN3"
    recovered.release()


def test_checkpoint_is_atomic_and_status_is_read_only(tmp_path: Path) -> None:
    runtime = SyntheticResearchDaemonRuntime([], budget_total=12, budget_used=3)
    daemon = ResearchDaemon(tmp_path, runtime=runtime, sleep_seconds=0)
    status = daemon.status_payload()
    assert status["daemon_state"] == ResearchDaemonState.BOOTSTRAP.value
    assert not (tmp_path / "reports/research_daemon/SYNTHETIC_OBJECTIVE/daemon_checkpoint.json").exists()


def test_canonical_objective_reader_ignores_orchestrator_only_fields(tmp_path: Path) -> None:
    objective_id = "OBJECTIVE_WITH_ACTIVATION_METADATA"
    payload = ResearchObjectiveV1.default(objective_id).to_dict()
    payload["activation_authorized"] = True
    path = tmp_path / "data/research/research_factory/objectives" / f"{objective_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    runtime = CanonicalResearchRuntime(tmp_path, objective_id=objective_id)

    assert runtime._objective().objective_id == objective_id


def test_windows_resource_monitor_has_native_fallback_without_psutil() -> None:
    if os.name != "nt":
        pytest.skip("Windows native resource fallback")
    rss, available = ResourceMonitorV1._windows_memory()
    assert isinstance(rss, int) and rss > 0
    assert isinstance(available, int) and available > 0


def test_canonical_context_reads_authoritative_budget_and_marks_repaired_b10_resumable(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime = CanonicalResearchRuntime(root)
    context = runtime.load_context()

    assert context.objective_id == "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1"
    assert context.budget["used"] == 12
    assert context.budget["total"] == 12
    assert context.budget["remaining"] == 0
    b10_id = "CAND_RELATIVE_STRENGTH_SLOPE_WITH_TRAILING_HIGH_STRUC_067151B6_V1_V2"
    b10 = runtime._contracts[b10_id]
    assert b10.candidate_hash == "99ca3066a26ad6143022234c3cd9b19abedd3a23c9773368534e7a47ef3ee450"
    assert b10_id not in runtime._engineering_blocked
    assert "CAND_LIQUIDITY_PREMIUM_PARTICIPATION_QUALITY_WITH_MED_981920D0_V1_V2" in runtime._completed
    assert runtime.next_candidate() is not None


def test_canonical_runtime_scopes_frozen_contracts_to_objective() -> None:
    root = Path(__file__).resolve().parents[2]
    runtime = CanonicalResearchRuntime(root)

    assert all(
        str((work.metadata["raw_contract"].get("policy_identity") or {}).get("objective_id"))
        == "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1"
        for work in runtime._contracts.values()
    )
    assert "CAND_MOMENTUM_V4_MOMENTUM_MOM_ACCEL_5_20_WITH_PRICE_V_95D59679_V1_V2" not in runtime._contracts


def test_canonical_runtime_binds_executor_and_reconciles_existing_trial_without_budget_delta() -> None:
    root = Path(__file__).resolve().parents[2]
    runtime = CanonicalResearchRuntime(root)
    assert isinstance(runtime.predictive_executor, CanonicalPredictiveExecutorV1)
    budget_path = root / "data/research/research_factory/batches/RUN_AUTONOMOUS_ALPHA_AFTER_SAMPLE_POLICY_V2_B01/search_budget_registry.json"
    before = SearchBudgetRegistryV1("RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1", budget_path).head_hash
    candidate_work = runtime._contracts["CAND_LIQUIDITY_PREMIUM_PARTICIPATION_QUALITY_WITH_MED_981920D0_V1_V2"]

    result = runtime.predictive_validate(candidate_work)

    after = SearchBudgetRegistryV1("RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1", budget_path).head_hash
    assert result.trial_id == "RUN_AUTONOMOUS_ALPHA_RESEARCH_NEW_BATCH_V1_B03_T001"
    assert result.details == {"canonical_trial_reconciled": True, "performance_rerun": False}
    assert after == before


def test_canonical_executor_trial_id_is_candidate_scoped() -> None:
    root = Path(__file__).resolve().parents[2]
    executor = CanonicalPredictiveExecutorV1(root, "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1")
    first = candidate("FIRST")
    second = candidate("SECOND")

    first_id = executor._new_trial_id("SHARED_BATCH", first)
    second_id = executor._new_trial_id("SHARED_BATCH", second)

    assert first_id != second_id
    assert first_id == "SHARED_BATCH_hash-FIRST_T001"
    assert second_id == "SHARED_BATCH_hash-SECOND_T001"


def test_canonical_structural_provider_uses_proven_partition_size(monkeypatch: pytest.MonkeyPatch) -> None:
    import chanlun_trader.research_daemon as daemon_module

    captured: dict[str, object] = {}

    class CapturingProvider:
        def __init__(self, root: Path, **kwargs: object) -> None:
            captured["provider_options"] = kwargs

        def __call__(self, candidate: object, policy: object) -> object:
            captured["candidate"] = candidate
            raise RuntimeError("provider fixture failure")

    monkeypatch.setattr(daemon_module, "RealSampleFeasibilityProviderV1", CapturingProvider)
    root = Path(__file__).resolve().parents[2]
    runtime = CanonicalResearchRuntime(root)
    b10 = runtime._contracts["CAND_RELATIVE_STRENGTH_SLOPE_WITH_TRAILING_HIGH_STRUC_067151B6_V1_V2"]

    result = runtime.structural_preflight(b10)

    assert result.reason_code == "STRUCTURAL_PROVIDER_ERROR"
    assert result.details == {"stage": "structural_provider", "error_type": "RuntimeError", "error": "provider fixture failure"}
    assert captured["provider_options"] == {"streaming": True, "partition_session_count": 80}
    provider_candidate = captured["candidate"]
    assert isinstance(provider_candidate, dict)
    assert provider_candidate["strategy_family"] in {"DAILY_EVENT", "DAILY_CROSS_SECTIONAL", "DAILY_FACTOR"}
    assert provider_candidate["candidate_hash"] == b10.candidate_hash
    assert provider_candidate["holding_period"] == b10.metadata["raw_contract"]["holding_period_trading_sessions"]
    assert provider_candidate["selection_rule"] == b10.metadata["raw_contract"]["selection_rule"]


def test_canonical_structural_preflight_rejects_non_executable_contract_before_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import chanlun_trader.research_daemon as daemon_module

    class UnexpectedProvider:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("provider must not be initialized for an incompatible frozen contract")

    monkeypatch.setattr(daemon_module, "RealSampleFeasibilityProviderV1", UnexpectedProvider)
    root = Path(__file__).resolve().parents[2]
    runtime = CanonicalResearchRuntime(root)
    source = runtime._contracts["CAND_RELATIVE_STRENGTH_SLOPE_WITH_TRAILING_HIGH_STRUC_067151B6_V1_V2"]
    raw_contract = dict(source.metadata["raw_contract"])
    raw_contract["family"] = "CORRECTED_V3_HISTORICAL"
    raw_contract["content_hash"] = stable_hash({key: value for key, value in raw_contract.items() if key != "content_hash"})
    incompatible = CandidateWork(
        source.candidate_id,
        source.candidate_hash,
        source.contract_ref,
        source.batch_id,
        source.mechanism,
        {**source.metadata, "raw_contract": raw_contract},
    )

    result = runtime.structural_preflight(incompatible)

    assert result.status == "ENGINEERING_BLOCKED"
    assert result.reason_code == "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE"
    assert result.details["stage"] == "structural_contract"
    assert "provider-supported DAILY family" in result.details["error"]


def test_structural_guard_allows_only_canonical_safety_prospective_paths() -> None:
    payload = {
        "lower_bound_integrity": {
            "checks": [
                {"evidence": {"architecture_safety": {"PROSPECTIVE": 0}}},
                {"evidence": {"safety": {"PROSPECTIVE": 0}}},
            ]
        }
    }

    PerformanceBlindGuard.assert_blind(payload, allowed_paths=STRUCTURAL_SAFETY_STATISTIC_ALLOWED_PATHS)

    with pytest.raises(PerformanceLeakError, match="PROSPECTIVE"):
        PerformanceBlindGuard.assert_blind({"PROSPECTIVE": 0}, allowed_paths=STRUCTURAL_SAFETY_STATISTIC_ALLOWED_PATHS)
