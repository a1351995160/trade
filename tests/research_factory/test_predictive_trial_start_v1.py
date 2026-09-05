from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from chanlun_trader.research_daemon import CanonicalPlatformContext, ResearchDaemon
from chanlun_trader.research_daemon_state import DaemonCheckpointStoreV1, ResearchDaemonState
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractRegistryV1, DurableFrozenCandidateContractV1
from chanlun_trader.research_factory.predictive_trial_start import PredictiveTrialStartError, PredictiveTrialStartServiceV1
from chanlun_trader.research_factory.predictive_trial_reauthorization import PredictiveTrialReauthorizationServiceV1
from chanlun_trader.research_factory.trial_adapter import ResearchFactoryTrialLedgerFacadeV1
from chanlun_trader.research.validation_policy_v2 import load_validation_decision_policy_v2


ROOT = Path(__file__).resolve().parents[2]
OBJECTIVE_ID = "SYNTHETIC_PREDICTIVE_TRIAL_START_V1"
CANDIDATE_ID = "CAND_PROMISING_FOLLOWUP_SENTIMENT_CONTINUATION_ONE_SHOT_20260901_01_V2"
CANDIDATE_HASH = "b23329cc98bddf1fa80ab9c467adcf013b02a29b5cddf6fcae9a1b92812521f6"
BATCH_ID = "SYNTHETIC_PREDICTIVE_TRIAL_START_B01"
FAMILY_ID = "MTF_SYNTHETIC_PREDICTIVE_TRIAL_START_V1"
CONTRACT_REF = "data/research/research_factory/batches/SYNTHETIC_CONTRACT/durable_frozen_candidate_contracts.json"
RECONCILIATION_REF = f"reports/research_daemon/{OBJECTIVE_ID}/structural_preflight_reconciliation.json"
RECONCILIATION_ID = "SYNTHETIC_STRUCTURAL_RECONCILIATION_V1"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _make_fixture(root: Path) -> dict[str, Path]:
    source_contract = json.loads((ROOT / "data/research/research_factory/batches/AI_HANDOFF_V2_e1ddf/durable_frozen_candidate_contracts.json").read_text(encoding="utf-8"))["contracts"][0]
    source_contract["policy_identity"] = {"objective_id": OBJECTIVE_ID}
    source_contract["source_provenance"] = {**source_contract["source_provenance"], "batch_id": BATCH_ID}
    source_contract["content_hash"] = stable_hash({key: value for key, value in source_contract.items() if key != "content_hash"})
    contract = DurableFrozenCandidateContractV1.from_dict(source_contract)
    contract_path = root / CONTRACT_REF
    registry = DurableFrozenCandidateContractRegistryV1(contract_path)
    registry.append(contract)
    registry.write()

    objective_path = root / "data/research/research_factory/objectives" / f"{OBJECTIVE_ID}.json"
    _write_json(
        objective_path,
        {
            "objective_id": OBJECTIVE_ID,
            "objective_identity_hash": stable_hash({"objective_id": OBJECTIVE_ID}),
            "multiple_testing_family_id": FAMILY_ID,
            "risk_constraints": {"prospective_access": "DISABLED", "real_order_execution": "DISABLED"},
        },
    )
    family_path = root / "data/research/research_factory/multiple_testing" / OBJECTIVE_ID / f"{FAMILY_ID}.json"
    _write_json(
        family_path,
        {
            "schema_version": "research-multiple-testing-family-v1",
            "family_id": FAMILY_ID,
            "objective_id": OBJECTIVE_ID,
            "hypothesis_slots": 1,
            "immutable_after_confirmation": True,
            "pre_registered_before_predictive_results": True,
        },
    )

    policy_path = root / "data/research/strategy_validation/validation_decision_policy_v2.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "data/research/strategy_validation/validation_decision_policy_v2.json", policy_path)
    shutil.copy2(ROOT / "data/research/strategy_validation/validation_decision_policy_v2.lock.json", root / "data/research/strategy_validation/validation_decision_policy_v2.lock.json")
    policy, policy_hash = load_validation_decision_policy_v2(policy_path)
    reconciliation_path = root / RECONCILIATION_REF
    _write_json(
        reconciliation_path,
        {
            "status": "PASS",
            "candidate_id": CANDIDATE_ID,
            "candidate_hash": CANDIDATE_HASH,
            "reconciliation_id": RECONCILIATION_ID,
            "repaired_structural_result": {
                "status": "PASS",
                "details": {
                    "v1_result": {
                        "policy_id": "VALIDATION_DECISION_POLICY_V2",
                        "policy_hash": policy_hash,
                        "policy_version": policy.policy_version,
                    }
                },
            },
        },
    )

    budget_path = root / "data/research/research_factory/batches" / BATCH_ID / "search_budget_registry.json"
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, budget_path)
    budget.register_objective(4)
    budget.register_batch(BATCH_ID, 4)
    budget.register_family(FAMILY_ID, 4)

    checkpoint_store = DaemonCheckpointStoreV1(root, OBJECTIVE_ID)
    checkpoint = {
        "schema_version": "research-daemon-checkpoint-v1",
        "daemon_run_id": "SYNTHETIC_DAEMON_RUN_V1",
        "objective_id": OBJECTIVE_ID,
        "current_state": ResearchDaemonState.READY.value,
        "current_candidate": None,
        "current_trial": None,
        "last_completed_candidate": {
            "batch_id": BATCH_ID,
            "candidate_id": CANDIDATE_ID,
            "candidate_hash": CANDIDATE_HASH,
            "contract_ref": CONTRACT_REF,
            "mechanism": "sentiment_event_continuation",
        },
        "required_action": "START_PREDICTIVE_TRIAL_1",
        "canonical_refs": {
            "structural_reconciliation": {"report_ref": RECONCILIATION_REF, "reconciliation_id": RECONCILIATION_ID},
            "last_structural_result": {"status": "PASS", "details": {"candidate_id": CANDIDATE_ID, "candidate_hash": CANDIDATE_HASH}},
        },
        "budget_view": {
            "objective_id": OBJECTIVE_ID,
            "registry_path": str(budget_path.relative_to(root)).replace("\\", "/"),
            "total": 4,
            "used": 0,
            "reserved": 0,
            "remaining": 4,
        },
        "ai_auto_invocation": "DISABLED",
        "no_new_predictive_trials_during_build": True,
        "retry_safe": True,
    }
    _write_json(checkpoint_store.checkpoint_path, checkpoint)

    authorization_path = root / "reports/research_orchestrator_v2" / OBJECTIVE_ID / "predictive_governance_decisions.jsonl"
    authorization_path.parent.mkdir(parents=True, exist_ok=True)
    authorization = {
                "decision_status": "AUTHORIZED",
                "decision_type": "AUTHORIZE_FIRST_PREDICTIVE_TRIAL",
                "next_action": "START_PREDICTIVE_TRIAL_1",
                "decision_id": "SYNTHETIC_AUTHORIZATION_DECISION_V1",
                "decision_hash": "SYNTHETIC_AUTHORIZATION_HASH_V1",
                "authorization_id": "SYNTHETIC_AUTHORIZATION_V1",
                "governance_decision_id": "SYNTHETIC_GOVERNANCE_DECISION_V1",
                "candidate_id": CANDIDATE_ID,
                "candidate_hash": CANDIDATE_HASH,
                "structural_reconciliation_id": RECONCILIATION_ID,
                "structural_status": "PASS",
                "structural": {"status": "PASS", "lower_bound": 59, "upper_bound": 59, "minimum_required": 30, "lower_bound_integrity": "PASS"},
                "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
                "prospective": "DISABLED",
                "real_order": "DISABLED",
    }
    authorization["decision_hash"] = stable_hash({key: value for key, value in authorization.items() if key != "decision_hash"})
    authorization_path.write_text(json.dumps(authorization, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return {"contract": contract_path, "family": family_path, "budget": budget_path, "checkpoint": checkpoint_store.checkpoint_path, "authorization": authorization_path}


def _service(root: Path, *, auto_run: bool = False) -> PredictiveTrialStartServiceV1:
    return PredictiveTrialStartServiceV1(root, auto_run=auto_run)


def _confirm_body(preview: dict[str, object], intent_id: str = "SYNTHETIC_START_INTENT_1") -> dict[str, object]:
    return {
        "confirmed": True,
        "action": "START_PREDICTIVE_TRIAL_1",
        "start_intent_id": intent_id,
        "candidate_id": preview["candidate_id"],
        "candidate_hash": preview["candidate_hash"],
        "preview_hash": preview["preview_hash"],
        "confirmation_token": preview["confirmation_token"],
    }


class _FormalRecoveryRuntime:
    """Minimal daemon runtime that must not select or execute a new Candidate."""

    def __init__(self, root: Path):
        self.root = root
        self.next_candidate_called = False

    def _budget(self) -> dict[str, int]:
        path = self.root / "data/research/research_factory/batches" / BATCH_ID / "search_budget_registry.json"
        snapshot = SearchBudgetRegistryV1(OBJECTIVE_ID, path).snapshot()
        bucket = next(item for item in snapshot["buckets"] if item["kind"] == "objective")
        return {"total": int(bucket["limit"]), "used": int(bucket["used"]), "reserved": int(bucket["reserved"]), "remaining": int(bucket["remaining"])}

    def load_context(self) -> CanonicalPlatformContext:
        return CanonicalPlatformContext(
            OBJECTIVE_ID,
            stable_hash({"objective_id": OBJECTIVE_ID}),
            {"objective_id": OBJECTIVE_ID, **self._budget()},
            "synthetic-capability",
            "synthetic-architecture",
            (CONTRACT_REF,),
            (RECONCILIATION_REF,),
        )

    def summary(self) -> dict[str, object]:
        return {"budget": self.load_context().budget, "current_trial": None, "last_completed_trial": None, "remaining_frozen_candidates": 1, "research_passed_count": 0, "promising_count": 0, "global_search_exhausted": False}

    def next_candidate(self):
        self.next_candidate_called = True
        raise AssertionError("formal Trial recovery must not select a new Candidate")


def test_start_is_exactly_once_and_reserves_canonical_budget_once(tmp_path: Path):
    paths = _make_fixture(tmp_path)
    service = _service(tmp_path)
    preview = service.preview(OBJECTIVE_ID)
    body = _confirm_body(preview)

    receipts = [service.confirm(OBJECTIVE_ID, body) for _ in range(10)]

    assert receipts[0]["idempotent"] is False
    assert all(item["trial_id"] == preview["preview"]["trial_id"] for item in receipts)
    assert all(item["status"] == "STARTED" for item in receipts)
    assert all(item["idempotent"] is True for item in receipts[1:])
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths["budget"]).snapshot()
    assert [(item["used"], item["reserved"], item["remaining"]) for item in budget["buckets"]] == [(0, 1, 3)] * 3
    trial_ledger = json.loads(paths["budget"].with_name("factory_trial_ledger.json").read_text(encoding="utf-8"))
    trial_ids = {event["trial_id"] for event in trial_ledger["events"]}
    assert trial_ids == {preview["preview"]["trial_id"]}
    registrations = json.loads(paths["family"].with_name(f"{paths['family'].stem}.trial_registrations.json").read_text(encoding="utf-8"))
    assert len(registrations["registrations"]) == 1
    assert registrations["registrations"][0]["candidate_hash"] == CANDIDATE_HASH
    checkpoint = DaemonCheckpointStoreV1(tmp_path, OBJECTIVE_ID).load()
    assert checkpoint is not None
    assert checkpoint.current_state == ResearchDaemonState.PREDICTIVE_RUNNING.value
    assert checkpoint.required_action == "PREDICTIVE_TRIAL_RUN_IN_PROGRESS"
    assert checkpoint.current_trial["trial_id"] == preview["preview"]["trial_id"]
    assert checkpoint.current_trial["performance_accessed"] is False


def test_restart_recovery_reuses_existing_trial_identity_without_double_reservation(tmp_path: Path):
    paths = _make_fixture(tmp_path)
    first = _service(tmp_path)
    preview = first.preview(OBJECTIVE_ID)
    body = _confirm_body(preview)
    first_receipt = first.confirm(OBJECTIVE_ID, body)

    restarted = _service(tmp_path)
    recovery = restarted.recover(OBJECTIVE_ID)
    repeated = restarted.confirm(OBJECTIVE_ID, body)

    assert recovery["trial_count"] == 1
    assert repeated["idempotent"] is True
    assert repeated["trial_id"] == first_receipt["trial_id"]
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths["budget"]).snapshot()
    assert sum(1 for item in budget["active_reservations"] if item == first_receipt["budget_reservation_identity"]) == 1
    assert len(json.loads(paths["budget"].with_name("factory_trial_ledger.json").read_text(encoding="utf-8"))["events"]) == 2


def test_trial_contract_is_immutable_and_binds_existing_authorization(tmp_path: Path):
    _make_fixture(tmp_path)
    service = _service(tmp_path)
    preview = service.preview(OBJECTIVE_ID)
    receipt = service.confirm(OBJECTIVE_ID, _confirm_body(preview, "SYNTHETIC_CONTRACT_BINDING_INTENT_1"))
    contract_path = tmp_path / receipt["trial_contract_ref"]
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    assert contract["immutable"] is True
    assert contract["candidate_id"] == CANDIDATE_ID
    assert contract["candidate_hash"] == CANDIDATE_HASH
    assert contract["authorization"]["authorization_decision_id"] == "SYNTHETIC_AUTHORIZATION_DECISION_V1"
    assert contract["authorization"]["authorization_decision_hash"]
    assert contract["structural"]["lower_bound"] == 59
    assert contract["structural"]["upper_bound"] == 59
    assert contract["structural"]["minimum_required"] == 30
    assert contract["multiple_testing"]["family_id"] == FAMILY_ID
    assert contract["data_snapshot"]["final_test_access"] == {"physical": 0, "analytical": 0, "decision": 0}

    changed = {**contract, "candidate_hash": "HASH_MUST_NOT_CHANGE"}
    with pytest.raises(PredictiveTrialStartError):
        service._write_immutable_contract(contract_path, changed)
    assert json.loads(contract_path.read_text(encoding="utf-8")) == contract


def test_concurrent_double_clicks_return_one_idempotent_trial(tmp_path: Path):
    _make_fixture(tmp_path)
    service = _service(tmp_path)
    preview = service.preview(OBJECTIVE_ID)
    body = _confirm_body(preview, "SYNTHETIC_CONCURRENT_START_INTENT_1")

    with ThreadPoolExecutor(max_workers=10) as executor:
        receipts = list(executor.map(lambda _: service.confirm(OBJECTIVE_ID, body), range(10)))

    assert {item["trial_id"] for item in receipts} == {preview["preview"]["trial_id"]}
    assert sum(not item["idempotent"] for item in receipts) == 1
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, tmp_path / "data/research/research_factory/batches" / BATCH_ID / "search_budget_registry.json").snapshot()
    assert sum(item["reserved"] for item in budget["buckets"]) == 3


def test_formal_api_uses_start_service_for_synthetic_objective(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _make_fixture(tmp_path)
    import chanlun_trader.webapp as webapp

    monkeypatch.setattr(webapp, "predictive_trial_start_service", _service(tmp_path))
    client = TestClient(webapp.app)
    prefix = f"/api/research-console/{OBJECTIVE_ID}/predictive/trial/start"
    preview_response = client.get(f"{prefix}/preview")
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["action"] == "START_PREDICTIVE_TRIAL_1"
    responses = [client.post(prefix, json=_confirm_body(preview, "SYNTHETIC_API_START_INTENT_1")) for _ in range(10)]
    assert all(response.status_code == 200 for response in responses)
    assert sum(response.json()["idempotent"] is False for response in responses) == 1
    assert all(response.json()["trial_id"] == preview["preview"]["trial_id"] for response in responses)
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, tmp_path / "data/research/research_factory/batches" / BATCH_ID / "search_budget_registry.json").snapshot()
    assert sum(item["reserved"] for item in budget["buckets"]) == 3


def test_browser_refresh_repeats_read_only_preview_without_creating_trial(tmp_path: Path):
    paths = _make_fixture(tmp_path)
    service = _service(tmp_path)

    first = service.preview(OBJECTIVE_ID)
    refreshed = service.preview(OBJECTIVE_ID)

    assert refreshed["preview"]["trial_id"] == first["preview"]["trial_id"]
    assert refreshed["preview_hash"] == first["preview_hash"]
    assert not (paths["authorization"].parent.parent.parent / "research_daemon" / OBJECTIVE_ID / "predictive_trial_start_intents.json").exists()
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths["budget"]).snapshot()
    assert all(item["used"] == 0 and item["reserved"] == 0 for item in budget["buckets"])


def test_daemon_restart_recovers_formal_trial_without_new_candidate_or_reservation(tmp_path: Path):
    paths = _make_fixture(tmp_path)
    service = _service(tmp_path)
    preview = service.preview(OBJECTIVE_ID)
    first = service.confirm(OBJECTIVE_ID, _confirm_body(preview, "SYNTHETIC_DAEMON_RESTART_INTENT_1"))
    runtime = _FormalRecoveryRuntime(tmp_path)
    restarted = ResearchDaemon(tmp_path, objective_id=OBJECTIVE_ID, runtime=runtime, sleep_seconds=0)
    restarted._predictive_trial_start_service = _service(tmp_path, auto_run=False)

    status = restarted.run_once()

    assert runtime.next_candidate_called is False
    assert status["predictive_trial_recovery"]["status"] == "RECOVERED"
    assert status["predictive_trial_recovery"]["trial_count"] == 1
    checkpoint = DaemonCheckpointStoreV1(tmp_path, OBJECTIVE_ID).load()
    assert checkpoint is not None
    assert checkpoint.current_trial["trial_id"] == first["trial_id"]
    assert checkpoint.required_action == "PREDICTIVE_TRIAL_RUN_IN_PROGRESS"
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths["budget"]).snapshot()
    assert sum(item["reserved"] for item in budget["buckets"]) == 3
    assert len(json.loads(paths["budget"].with_name("factory_trial_ledger.json").read_text(encoding="utf-8"))["events"]) == 2


def test_runner_failure_before_performance_releases_reserved_budget(tmp_path: Path):
    paths = _make_fixture(tmp_path)

    class FailingRunner:
        def run(self, candidate):
            raise RuntimeError("synthetic result persistence failure before performance access")

    service = PredictiveTrialStartServiceV1(tmp_path, runner=FailingRunner(), auto_run=False)
    preview = service.preview(OBJECTIVE_ID)
    receipt = service.confirm(OBJECTIVE_ID, _confirm_body(preview, "SYNTHETIC_FAILURE_BEFORE_PERFORMANCE_1"))
    service._run_intent(OBJECTIVE_ID, receipt["start_intent_id"])

    record = service._trial_records(OBJECTIVE_ID)[receipt["trial_id"]]
    assert record["status"] == "BLOCKED"
    assert record["performance_accessed"] is False
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths["budget"]).snapshot()
    assert all(item["used"] == 0 and item["reserved"] == 0 and item["remaining"] == 4 for item in budget["buckets"])
    intent = json.loads((tmp_path / "reports/research_daemon" / OBJECTIVE_ID / "predictive_trial_start_intents.json").read_text(encoding="utf-8"))["intents"][receipt["start_intent_id"]]
    assert intent["stage"] == "TRIAL_FAILED"
    assert intent["performance_accessed"] is False


def test_runner_failure_after_performance_consumes_reserved_budget_once(tmp_path: Path):
    paths = _make_fixture(tmp_path)

    class AccessThenFailRunner:
        def run(self, candidate):
            ledger = ResearchFactoryTrialLedgerFacadeV1(path=paths["budget"].with_name("factory_trial_ledger.json"))
            ledger.mark_performance_accessed(f"{BATCH_ID}_{CANDIDATE_HASH}_T001")
            raise RuntimeError("synthetic daemon crash after performance access")

    service = PredictiveTrialStartServiceV1(tmp_path, runner=AccessThenFailRunner(), auto_run=False)
    preview = service.preview(OBJECTIVE_ID)
    receipt = service.confirm(OBJECTIVE_ID, _confirm_body(preview, "SYNTHETIC_FAILURE_AFTER_PERFORMANCE_1"))
    service._run_intent(OBJECTIVE_ID, receipt["start_intent_id"])

    record = service._trial_records(OBJECTIVE_ID)[receipt["trial_id"]]
    assert record["status"] == "INVALIDATED"
    assert record["performance_accessed"] is True
    assert record["lineage"]["budget_consumption"] == "CONSUMED"
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths["budget"]).snapshot()
    assert all(item["used"] == 1 and item["reserved"] == 0 and item["remaining"] == 3 for item in budget["buckets"])


@pytest.mark.parametrize("mutation", ["structural_unknown", "structural_blocked", "governance_not_authorized", "candidate_hash_mismatch", "candidate_superseded", "budget_exhausted", "final_test_open", "invalid_objective_state"])
def test_start_fails_closed_when_any_canonical_gate_is_invalid(tmp_path: Path, mutation: str):
    paths = _make_fixture(tmp_path)
    if mutation in {"structural_unknown", "structural_blocked", "governance_not_authorized", "candidate_hash_mismatch", "final_test_open"}:
        row = json.loads(paths["authorization"].read_text(encoding="utf-8"))
        if mutation == "structural_unknown":
            row["structural_status"] = "UNKNOWN"
            row["structural"]["status"] = "UNKNOWN"
        elif mutation == "structural_blocked":
            row["structural_status"] = "BLOCKED"
            row["structural"]["status"] = "BLOCKED"
        elif mutation == "governance_not_authorized":
            row["decision_status"] = "PENDING_HUMAN_DECISION"
        elif mutation == "candidate_hash_mismatch":
            row["candidate_hash"] = "HASH_MISMATCH"
        else:
            row["final_test_access"]["physical"] = 1
        paths["authorization"].write_text(json.dumps(row) + "\n", encoding="utf-8")
    elif mutation == "candidate_superseded":
        checkpoint = json.loads(paths["checkpoint"].read_text(encoding="utf-8"))
        checkpoint["last_completed_candidate"]["candidate_id"] = "CANDIDATE_SUPERSEDED"
        _write_json(paths["checkpoint"], checkpoint)
    elif mutation == "budget_exhausted":
        budget = json.loads(paths["budget"].read_text(encoding="utf-8"))
        for bucket in budget["buckets"]:
            bucket["used"] = bucket["limit"]
            bucket["remaining"] = 0
        _write_json(paths["budget"], budget)
    else:
        checkpoint = json.loads(paths["checkpoint"].read_text(encoding="utf-8"))
        checkpoint["current_state"] = ResearchDaemonState.PREDICTIVE_COMPLETE.value
        _write_json(paths["checkpoint"], checkpoint)

    service = _service(tmp_path)
    with pytest.raises(PredictiveTrialStartError):
        service.preview(OBJECTIVE_ID)
    assert not list((tmp_path / "data/research/research_factory/batches").glob("*/factory_trial_ledger.json"))


def test_start_rejects_second_trial_when_first_trial_already_exists(tmp_path: Path):
    _make_fixture(tmp_path)
    service = _service(tmp_path)
    preview = service.preview(OBJECTIVE_ID)
    service.confirm(OBJECTIVE_ID, _confirm_body(preview))

    with pytest.raises(PredictiveTrialStartError):
        service.preview(OBJECTIVE_ID)


def test_same_trial_resume_is_exactly_once_and_reuses_consumed_budget_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _make_fixture(tmp_path)

    class AccessThenFailRunner:
        def run(self, candidate):
            ledger = ResearchFactoryTrialLedgerFacadeV1(path=paths["budget"].with_name("factory_trial_ledger.json"))
            ledger.mark_performance_accessed(f"{BATCH_ID}_{CANDIDATE_HASH}_T001")
            raise RuntimeError("synthetic event materialization failure after performance access")

    failing = PredictiveTrialStartServiceV1(tmp_path, runner=AccessThenFailRunner(), auto_run=False)
    start_preview = failing.preview(OBJECTIVE_ID)
    start_receipt = failing.confirm(OBJECTIVE_ID, _confirm_body(start_preview, "SYNTHETIC_RESUME_START_INTENT_1"))
    failing._run_intent(OBJECTIVE_ID, start_receipt["start_intent_id"])

    class RecoveryRunner:
        def __init__(self):
            self.calls: list[bool] = []

        def run(self, candidate, *, recovery=False):
            self.calls.append(bool(recovery))
            ledger = ResearchFactoryTrialLedgerFacadeV1(path=paths["budget"].with_name("factory_trial_ledger.json"))
            ledger.mark_provisional(
                start_receipt["trial_id"],
                local_classification="PROMISING",
                evidence_ref="reports/synthetic_resume_evidence.json",
                result={"performance_completed": True},
            )
            ledger.mark_final_adjudication(
                start_receipt["trial_id"],
                "PROMISING",
                decision_id="SYNTHETIC_RESUME_DECISION_1",
                result={"final_test_access": {"physical": 0, "analytical": 0, "decision": 0}},
            )
            return SimpleNamespace(status="COMPLETED", performance_accessed=True, classification="PROMISING")

    runner = RecoveryRunner()
    service = PredictiveTrialStartServiceV1(tmp_path, runner=runner, auto_run=False)
    monkeypatch.setattr(
        service,
        "_event_materialization_audit",
        lambda context: {
            "status": "PASS",
            "event_ids": ["BENCHMARK_SENTIMENT_BREAKOUT_EVENT_V1"],
            "event_day_count": 137,
            "unresolved_event_ids": [],
        },
    )
    preview = service.resume_preview(OBJECTIVE_ID)
    body = {
        "confirmed": True,
        "action": "RESUME_PREDICTIVE_TRIAL_1",
        "start_intent_id": start_receipt["start_intent_id"],
        "trial_id": start_receipt["trial_id"],
        "candidate_id": preview["candidate_id"],
        "candidate_hash": preview["candidate_hash"],
        "preview_hash": preview["preview_hash"],
        "confirmation_token": preview["confirmation_token"],
    }

    with ThreadPoolExecutor(max_workers=10) as executor:
        receipts = list(executor.map(lambda _: service.confirm_resume(OBJECTIVE_ID, body), range(10)))

    assert sum(item["idempotent"] is False for item in receipts) == 1
    assert sum(item["idempotent"] is True for item in receipts) == 9
    assert {item["trial_id"] for item in receipts} == {start_receipt["trial_id"]}
    assert {item["candidate_hash"] for item in receipts} == {CANDIDATE_HASH}
    assert all(item["budget"]["used"] == 1 and item["budget"]["reserved"] == 0 and item["budget"]["remaining"] == 3 for item in receipts)

    service._run_intent(OBJECTIVE_ID, start_receipt["start_intent_id"])
    completed = service.confirm_resume(OBJECTIVE_ID, body)
    assert completed["status"] == "COMPLETED"
    assert completed["idempotent"] is True
    assert runner.calls == [True]

    ledger_payload = json.loads(paths["budget"].with_name("factory_trial_ledger.json").read_text(encoding="utf-8"))
    trial_events = [item for item in ledger_payload["events"] if item["trial_id"] == start_receipt["trial_id"]]
    assert len({item["trial_id"] for item in trial_events}) == 1
    assert sum(item["event_type"] == "ENGINEERING_RESUME_STARTED" for item in trial_events) == 1
    registrations = json.loads(paths["family"].with_name(f"{paths['family'].stem}.trial_registrations.json").read_text(encoding="utf-8"))
    assert len(registrations["registrations"]) == 1
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths["budget"]).snapshot()
    assert all(item["used"] == 1 and item["reserved"] == 0 and item["remaining"] == 3 for item in budget["buckets"])


def test_same_trial_resume_failure_keeps_budget_and_records_failure_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _make_fixture(tmp_path)

    class AccessThenFailRunner:
        def run(self, candidate):
            ledger = ResearchFactoryTrialLedgerFacadeV1(path=paths["budget"].with_name("factory_trial_ledger.json"))
            ledger.mark_performance_accessed(f"{BATCH_ID}_{CANDIDATE_HASH}_T001")
            raise RuntimeError("synthetic initial event materialization failure")

    failing = PredictiveTrialStartServiceV1(tmp_path, runner=AccessThenFailRunner(), auto_run=False)
    start = failing.confirm(OBJECTIVE_ID, _confirm_body(failing.preview(OBJECTIVE_ID), "SYNTHETIC_RESUME_FAILURE_START_1"))
    failing._run_intent(OBJECTIVE_ID, start["start_intent_id"])

    class RecoveryFailRunner:
        def __init__(self):
            self.calls: list[bool] = []

        def run(self, candidate, *, recovery=False):
            self.calls.append(bool(recovery))
            raise RuntimeError("synthetic recovery event materialization failure")

    runner = RecoveryFailRunner()
    service = PredictiveTrialStartServiceV1(tmp_path, runner=runner, auto_run=False)
    monkeypatch.setattr(
        service,
        "_event_materialization_audit",
        lambda context: {"status": "PASS", "event_ids": ["BENCHMARK_SENTIMENT_BREAKOUT_EVENT_V1"], "event_day_count": 137, "unresolved_event_ids": []},
    )
    preview = service.resume_preview(OBJECTIVE_ID)
    body = {
        "confirmed": True,
        "action": "RESUME_PREDICTIVE_TRIAL_1",
        "start_intent_id": start["start_intent_id"],
        "trial_id": start["trial_id"],
        "candidate_id": preview["candidate_id"],
        "candidate_hash": preview["candidate_hash"],
        "preview_hash": preview["preview_hash"],
        "confirmation_token": preview["confirmation_token"],
    }

    requested = service.confirm_resume(OBJECTIVE_ID, body)
    service._run_intent(OBJECTIVE_ID, start["start_intent_id"])
    repeated = service.confirm_resume(OBJECTIVE_ID, body)

    record = service._trial_records(OBJECTIVE_ID)[start["trial_id"]]
    assert requested["trial_id"] == repeated["trial_id"] == start["trial_id"]
    assert repeated["status"] == "FAILED"
    assert repeated["idempotent"] is True
    assert runner.calls == [True]
    assert record["status"] == "INVALIDATED"
    assert record["lineage"]["engineering_resume_attempt_count"] == 1
    assert record["lineage"]["engineering_attempt_count"] == 2
    assert len(record["lineage"]["engineering_failure_history"]) == 2
    assert record["lineage"]["engineering_failure_history"][-1]["error_code"] == "PREDICTIVE_TRIAL_RUNNER_ERROR"
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths["budget"]).snapshot()
    assert all(item["used"] == 1 and item["reserved"] == 0 and item["remaining"] == 3 for item in budget["buckets"])
    ledger_payload = json.loads(paths["budget"].with_name("factory_trial_ledger.json").read_text(encoding="utf-8"))
    trial_events = [item for item in ledger_payload["events"] if item["trial_id"] == start["trial_id"]]
    assert sum(item["event_type"] == "ENGINEERING_RESUME_STARTED" for item in trial_events) == 1
    assert sum(item["event_type"] == "ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS" for item in trial_events) == 2
    registrations = json.loads(paths["family"].with_name(f"{paths['family'].stem}.trial_registrations.json").read_text(encoding="utf-8"))
    assert len(registrations["registrations"]) == 1


def test_formal_resume_api_is_local_and_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _make_fixture(tmp_path)

    class AccessThenFailRunner:
        def run(self, candidate):
            ledger = ResearchFactoryTrialLedgerFacadeV1(path=paths["budget"].with_name("factory_trial_ledger.json"))
            ledger.mark_performance_accessed(f"{BATCH_ID}_{CANDIDATE_HASH}_T001")
            raise RuntimeError("synthetic event materialization failure")

    failing = PredictiveTrialStartServiceV1(tmp_path, runner=AccessThenFailRunner(), auto_run=False)
    start = failing.confirm(OBJECTIVE_ID, _confirm_body(failing.preview(OBJECTIVE_ID), "SYNTHETIC_API_RESUME_START_1"))
    failing._run_intent(OBJECTIVE_ID, start["start_intent_id"])

    service = PredictiveTrialStartServiceV1(tmp_path, auto_run=False)
    monkeypatch.setattr(
        service,
        "_event_materialization_audit",
        lambda context: {"status": "PASS", "event_ids": ["BENCHMARK_SENTIMENT_BREAKOUT_EVENT_V1"], "event_day_count": 137, "unresolved_event_ids": []},
    )
    monkeypatch.setattr(__import__("chanlun_trader.webapp", fromlist=["predictive_trial_start_service"]), "predictive_trial_start_service", service)
    import chanlun_trader.webapp as webapp

    client = TestClient(webapp.app)
    prefix = f"/api/research-console/{OBJECTIVE_ID}/predictive/trial/resume"
    response = client.get(f"{prefix}/preview")
    assert response.status_code == 200
    preview = response.json()
    body = {
        "confirmed": True,
        "action": preview["action"],
        "start_intent_id": preview["start_intent_id"],
        "trial_id": preview["trial_id"],
        "candidate_id": preview["candidate_id"],
        "candidate_hash": preview["candidate_hash"],
        "preview_hash": preview["preview_hash"],
        "confirmation_token": preview["confirmation_token"],
    }
    first = client.post(prefix, json=body)
    second = client.post(prefix, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["trial_id"] == second.json()["trial_id"] == start["trial_id"]
    assert first.json()["idempotent"] is False
    assert second.json()["idempotent"] is True
    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths["budget"]).snapshot()
    assert all(item["used"] == 1 and item["reserved"] == 0 and item["remaining"] == 3 for item in budget["buckets"])


def test_empty_event_contract_is_a_pass_for_event_materialization_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    class EmptyEventProvider:
        def __init__(self, root: Path, *, streaming: bool):
            self._audit: list[str] = []
            self.benchmark_coverage = {}

        def _load_daily(self, start_date: int, end_date: int, warmup_start: int):
            return []

        def materialize_event_rows(self, conditions, daily, start_date, end_date, *, record):
            assert conditions == []
            return {}, {}

        def event_source_identity(self):
            return {"source": "empty-event-fixture"}

    monkeypatch.setattr(
        "chanlun_trader.research_factory.real_sample_feasibility.RealSampleFeasibilityProviderV1",
        EmptyEventProvider,
    )
    service = PredictiveTrialStartServiceV1(tmp_path, auto_run=False)
    snapshot = SimpleNamespace(
        policy=SimpleNamespace(research_end=20250731),
        candidate=SimpleNamespace(provider_candidate_payload=lambda: {"event_conditions": []}),
    )

    result = service._event_materialization_audit({"snapshot": snapshot})

    assert result["status"] == "PASS"
    assert result["event_ids"] == []
    assert result["unresolved_event_ids"] == []
    assert result["event_day_count"] == 0


def test_reauthorized_new_trial_has_new_governance_budget_and_trial_identity(tmp_path: Path):
    paths = _make_fixture(tmp_path)
    family = json.loads(paths["family"].read_text(encoding="utf-8"))
    family["hypothesis_slots"] = 2
    _write_json(paths["family"], family)
    governance_path = tmp_path / "reports/research_orchestrator_v2" / OBJECTIVE_ID / "structural_governance_decision_required.json"
    _write_json(
        governance_path,
        {
            "schema_version": "predictive-governance-preview-v1",
            "objective_id": OBJECTIVE_ID,
            "decision_id": "SYNTHETIC_STRUCTURAL_DECISION_V1",
            "decision_mode": "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED",
            "status": "PENDING_HUMAN_DECISION",
            "candidate_id": CANDIDATE_ID,
            "candidate_hash": CANDIDATE_HASH,
            "reconciliation_id": RECONCILIATION_ID,
            "structural": {"status": "PASS", "lower_bound": 59, "upper_bound": 59, "minimum_required": 30, "lower_bound_integrity": "PASS"},
            "final_test_access": {"analytical": 0, "decision": 0, "physical": 0},
            "prospective": "DISABLED",
            "real_order": "DISABLED",
        },
    )

    class AccessThenFailRunner:
        def run(self, candidate):
            ledger = ResearchFactoryTrialLedgerFacadeV1(path=paths["budget"].with_name("factory_trial_ledger.json"))
            ledger.mark_performance_accessed(f"{BATCH_ID}_{CANDIDATE_HASH}_T001")
            raise RuntimeError("synthetic engineering interruption")

    initial = PredictiveTrialStartServiceV1(tmp_path, runner=AccessThenFailRunner(), auto_run=False)
    first = initial.confirm(OBJECTIVE_ID, _confirm_body(initial.preview(OBJECTIVE_ID), "SYNTHETIC_OLD_START"))
    initial._run_intent(OBJECTIVE_ID, first["start_intent_id"])
    repair_path = tmp_path / "reports/GOVERNED_NEW_MECHANISM_RANK_ONLY_SEMANTIC_REPAIR_V1.json"
    _write_json(
        repair_path,
        {
            "status": "PASS",
            "scope": {"objective_id": OBJECTIVE_ID, "candidate_id": CANDIDATE_ID, "candidate_hash": CANDIDATE_HASH},
            "repair": {"status": "PASS", "candidate_contract_unchanged": True, "factor_cache_unchanged": True},
        },
    )

    service = PredictiveTrialReauthorizationServiceV1(tmp_path, auto_run=False)
    authorization_preview = service.preview_authorization(OBJECTIVE_ID)
    authorization_body = {
        "confirmed": True,
        "action": "AUTHORIZE_NEW_PREDICTIVE_TRIAL",
        "decision_type": "AUTHORIZE_NEW_PREDICTIVE_TRIAL",
        "authorization_id": "SYNTHETIC_NEW_AUTHORIZATION",
        "candidate_id": authorization_preview["candidate_id"],
        "candidate_hash": authorization_preview["candidate_hash"],
        "preview_hash": authorization_preview["preview_hash"],
        "confirmation_token": authorization_preview["confirmation_token"],
    }
    authorization = service.confirm_authorization(OBJECTIVE_ID, authorization_body)
    repeated_authorization = service.confirm_authorization(OBJECTIVE_ID, authorization_body)

    assert authorization["idempotent"] is False
    assert repeated_authorization["idempotent"] is True
    assert authorization["decision"]["prior_trial_id"] == first["trial_id"]
    assert authorization["decision"]["trial_created"] is False
    assert authorization["decision"]["budget_reserved"] == 0

    start_preview = service.preview_start_new_trial(OBJECTIVE_ID)
    start_body = {
        "confirmed": True,
        "action": "START_PREDICTIVE_TRIAL_2",
        "start_intent_id": "SYNTHETIC_NEW_START",
        "candidate_id": start_preview["candidate_id"],
        "candidate_hash": start_preview["candidate_hash"],
        "preview_hash": start_preview["preview_hash"],
        "confirmation_token": start_preview["confirmation_token"],
    }
    started = service.confirm_start_new_trial(OBJECTIVE_ID, start_body)
    repeated_start = service.confirm_start_new_trial(OBJECTIVE_ID, start_body)

    assert started["idempotent"] is False
    assert repeated_start["idempotent"] is True
    assert started["trial_number"] == repeated_start["trial_number"] == 2
    assert started["trial_id"].endswith("_T002")
    assert repeated_start["trial_id"] == started["trial_id"]

    budget = SearchBudgetRegistryV1(OBJECTIVE_ID, paths["budget"]).snapshot()
    assert [(item["used"], item["reserved"], item["remaining"]) for item in budget["buckets"]] == [(1, 1, 2)] * 3
    registrations = json.loads(paths["family"].with_name(f"{paths['family'].stem}.trial_registrations.json").read_text(encoding="utf-8"))
    assert [(item["trial_number"], item["trial_id"]) for item in registrations["registrations"]] == [(1, first["trial_id"]), (2, started["trial_id"])]
    trial_records = service._trial_records(OBJECTIVE_ID)
    assert set(trial_records) == {first["trial_id"], started["trial_id"]}
    assert trial_records[first["trial_id"]]["status"] == "INVALIDATED"
    assert trial_records[started["trial_id"]]["status"] == "REGISTERED"
    assert trial_records[started["trial_id"]]["lineage"]["canonical_start_action"] == "START_PREDICTIVE_TRIAL_2"
