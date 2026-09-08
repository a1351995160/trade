from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import ValidationError, validate as validate_json_schema

from chanlun_trader.research_factory.autonomous_orchestrator_v2 import (
    AIBatchValidationError,
    AIBatchValidatorV2,
    AIInvocationError,
    AutonomousResearchOrchestratorV2,
    CanonicalOrchestratorRuntimeV2,
    CanonicalResearchSnapshotV2,
    MANUAL_HANDOFF_LEGACY_CONTEXT_COMPATIBILITY,
    OrchestratorConfigV2,
    OrchestratorControlServiceV1,
    OrchestratorState,
    SyntheticAutonomousResearchRuntimeV2,
    SyntheticCodexBatchInvokerV2,
)
from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research.strategy_candidate import StrategyCandidateSpec
from chanlun_trader.research.strategy_semantic import ExitPredicateSpec, SignalPredicateSpec


def _candidate(candidate_id: str, *, mechanism: str = "local", candidate_hash: str | None = None) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_hash": candidate_hash or f"HASH_{candidate_id}",
        "family_id": mechanism,
        "mechanism": mechanism,
        "factor_ids": ["FACTOR_A"],
        "event_ids": [],
        "holding_period_days": 5,
    }


def _config(**overrides):
    values = {"ai_invocation_mode": "AUTO_CODEX", "backoff_seconds": 0, "max_steps": 32}
    values.update(overrides)
    return OrchestratorConfigV2(**values)


def test_web_start_ai_uses_existing_activation_and_is_idempotency_safe(tmp_path, monkeypatch):
    objective_id = "OBJECTIVE_START_AI_TEST"
    run_dir = tmp_path / "reports" / "research_orchestrator_v2" / objective_id
    run_dir.mkdir(parents=True)
    (run_dir / "process_launch.json").write_text(json.dumps({"execution_id": "EXECUTION_START_AI_TEST"}), encoding="utf-8")

    class FakeStore:
        def __init__(self):
            self.run_dir = run_dir

        def load_invocation(self):
            return {"status": "FAILED", "retryable": True, "next_retry_at": "2020-01-01T00:00:00+00:00"}

        def atomic_write(self, path, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")

    class FakeStatus:
        def to_dict(self):
            return {"orchestrator_state": OrchestratorState.AI_INVOCATION_PENDING.value}

    class FakeOrchestrator:
        def __init__(self):
            self.root = tmp_path.resolve()
            self.objective_id = objective_id
            self.store = FakeStore()

        def status(self):
            return FakeStatus()

    calls = []

    class FakeLauncher:
        def __init__(self, root):
            assert root == tmp_path.resolve()

        def live_pid(self, _objective_id):
            return None

        def start(self, objective_id, *, execution_id):
            calls.append((objective_id, execution_id))
            return {"status": "STARTED", "objective_id": objective_id, "execution_id": execution_id}

    monkeypatch.setattr("chanlun_trader.research_factory.orchestrator_launcher.OrchestratorProcessLauncherV1", FakeLauncher)
    service = OrchestratorControlServiceV1(tmp_path)
    fake_orchestrator = FakeOrchestrator()
    monkeypatch.setattr(service, "_orchestrator", lambda _objective_id: fake_orchestrator)

    actions = {item["action"]: item for item in service.operations(objective_id)["actions"]}
    assert actions["START_AI"]["available"] is True

    response = service.execute(objective_id, "START_AI", idempotency_key="start-ai-1", confirmed=True)

    assert response["status"] == "ACCEPTED"
    assert response["orchestrator_activation"]["status"] == "STARTED"
    assert calls == [(objective_id, "EXECUTION_START_AI_TEST")]

    repeated = service.execute(objective_id, "START_AI", idempotency_key="start-ai-1", confirmed=True)

    assert repeated["idempotent"] is True
    assert calls == [(objective_id, "EXECUTION_START_AI_TEST")]


def test_synthetic_long_run_runs_local_ai_local_then_closes_out(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2(
        "SYNTHETIC_OBJECTIVE",
        budget_total=2,
        initial_candidates=[_candidate("LOCAL_001")],
    )
    invoker = SyntheticCodexBatchInvokerV2()

    result = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=invoker,
        config=_config(),
    ).run()

    assert result["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert result["daemon_state"] == "BUDGET_EXHAUSTED"
    assert runtime.local_calls == 2
    assert runtime.ai_batches == 1
    assert invoker.calls == 1
    assert result["budget"]["used"] == result["budget"]["total"] == 2
    assert json.loads((tmp_path / "reports" / "RESEARCH_GOVERNANCE_DECISION_REQUIRED.json").read_text(encoding="utf-8"))["allowed_choices"]


def test_structural_pass_predictive_authorization_boundary_stops_orchestrator_and_projects_human_action(tmp_path):
    objective_id = "OBJECTIVE_STRUCTURAL_PASS_AUTHORIZATION"
    snapshot = CanonicalResearchSnapshotV2(
        objective_id=objective_id,
        objective_hash="OBJECTIVE_HASH",
        daemon_state="READY",
        daemon_run_id="DAEMON_1",
        budget={"total": 6, "used": 0, "reserved": 0, "remaining": 6},
        candidates=(_candidate("CAND_STRUCTURAL_PASS"),),
        remaining_frozen_candidates=1,
        required_action="PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED",
    )

    class BoundaryRuntime:
        def __init__(self):
            self.local_calls = 0

        def snapshot(self):
            return snapshot

        def run_local(self):
            self.local_calls += 1
            raise AssertionError("local research must not rerun across predictive authorization boundary")

        def ingest_ai_batch(self, manifest_path, manifest, *, staging_dir=None):
            raise AssertionError("AI ingestion must not run across predictive authorization boundary")

    runtime = BoundaryRuntime()
    orchestrator = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=objective_id,
        runtime=runtime,
        config=_config(),
    )
    orchestrator.checkpoint["state"] = OrchestratorState.LOCAL_RESEARCH_RUNNING.value
    structural_governance = orchestrator.store.run_dir / "structural_governance_decision_required.json"
    structural_governance.parent.mkdir(parents=True, exist_ok=True)
    structural_governance.write_text(
        json.dumps({
            "status": "PENDING_HUMAN_DECISION",
            "candidate_id": "CAND_STRUCTURAL_PASS",
            "next_action": "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED",
        }),
        encoding="utf-8",
    )

    projected = orchestrator.status().to_dict()
    assert projected["orchestrator_state"] == OrchestratorState.ACTIVE.value
    assert projected["next_action"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert projected["next_action_zh"] == "结构预检已通过，等待你授权第 1 次预测试验"
    assert projected["waiting_for_governance"] is True
    assert projected["governance_decision_state"] == "REQUIRED"

    result = orchestrator.step()

    assert result["orchestrator_state"] == OrchestratorState.ACTIVE.value
    assert result["next_action"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"
    assert result["waiting_for_governance"] is True
    assert runtime.local_calls == 0
    assert result["budget"] == {"total": 6, "used": 0, "reserved": 0, "remaining": 6}
    assert orchestrator.checkpoint["state"] == OrchestratorState.ACTIVE.value
    assert orchestrator.checkpoint["last_transition"]["reason_code"] == "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED"


def _manual_result(handoff: dict, objective_id: str, invocation_id: str, *, candidate_id: str = "AI_MANUAL_CANDIDATE") -> dict:
    candidate_hash = f"HASH_{candidate_id}"
    semantic_fingerprint = f"SEMANTIC_{candidate_id}"
    contract = {
        "candidate_id": candidate_id,
        "candidate_hash": candidate_hash,
        "hypothesis_id": f"HYPOTHESIS_{candidate_id}",
        "hypothesis_fingerprint": f"HYPOTHESIS_FINGERPRINT_{candidate_id}",
        "semantic_fingerprint": semantic_fingerprint,
        "full_semantic_record": {"candidate": {"candidate_id": candidate_id, "preregistration_hash": candidate_hash}, "preregistration_hash": candidate_hash, "semantic_fingerprint": semantic_fingerprint},
        "family": "synthetic_new",
        "mechanism": "synthetic_new",
        "factor_ids": ["SYNTHETIC_FACTOR"],
        "factor_roles": [{"factor_id": "SYNTHETIC_FACTOR", "role": "SIGNAL", "direction": "POSITIVE"}],
        "factor_directions": {"SYNTHETIC_FACTOR": "POSITIVE"},
        "event_ids": [],
        "event_timing_semantics": {},
        "entry_predicate": {"type": "FACTOR_THRESHOLD", "factor_id": "SYNTHETIC_FACTOR"},
        "confirmation_predicate": {},
        "interaction_semantics": {"eligibility_conditions": [], "interaction_conditions": []},
        "ranking_semantics": {},
        "selection_rule": {"type": "TOP_N", "top_n": 1},
        "top_n": 1,
        "max_positions": 1,
        "holding_period_trading_sessions": 5,
        "entry_timing": {"type": "NEXT_SESSION_OPEN"},
        "exit_contract": {"type": "FIXED_HOLD", "sessions": 5},
        "execution_contract_version": "SYNTHETIC_EXECUTION_V1",
        "t_plus_1_contract": {"enabled": True},
        "capital_product_contract_identity": {"contract_id": "SYNTHETIC_CAPITAL_V1"},
        "fee_slippage_contract_references": {"contract_id": "SYNTHETIC_COST_V1"},
        "pit_dependencies": {"universe_rule": {"market": ["SH", "SZ"]}, "pit_required": True},
        "factor_event_registry_identities": {"factor_registry": "SYNTHETIC_FACTOR_REGISTRY_V1"},
        "research_period_identity": {"period_id": "SYNTHETIC_RESEARCH_PERIOD_V1"},
        "policy_identity": {"objective_id": objective_id},
        "created_frozen_timestamp": handoff["created_at"],
        "source_provenance": {"handoff_id": handoff["handoff_id"]},
        "contract_schema_version": "durable-frozen-candidate-contract-v1",
    }
    contract["content_hash"] = stable_hash(contract)
    return {
        "schema_version": "ai-research-batch-result-v2",
        "handoff_id": handoff["handoff_id"],
        "objective_id": objective_id,
        "ai_invocation_id": invocation_id,
        "batch_id": "AI_MANUAL_BATCH_001",
        "candidate_ids": [candidate_id],
        "candidate_hashes": {candidate_id: candidate_hash},
        "candidates": [{"candidate_id": candidate_id, "candidate_hash": candidate_hash, "family_id": "synthetic_new", "mechanism": "synthetic_new", "factor_ids": ["SYNTHETIC_FACTOR"], "event_ids": [], "holding_period_days": 5}],
        "candidate_contracts": [contract],
        "durable_contract_refs": ["inline:candidate_contracts[0]"],
        "artifact_refs": [],
        "validation_status": "VALID",
        "no_outcome_compliance_status": "PASS",
    }


def _schema_fixture(schema: dict, field_name: str = ""):
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties", {})
        value = {name: _schema_fixture(properties[name], name) for name in schema.get("required", ())}
        if len(value) < int(schema.get("minProperties", 0)) and schema.get("additionalProperties", True) is not False:
            value.setdefault("synthetic", "SYNTHETIC_VALUE")
        return value
    if schema_type == "array":
        items = schema.get("items", {"type": "string"})
        return [_schema_fixture(items, field_name) for _ in range(int(schema.get("minItems", 0)))]
    if schema_type == "integer":
        return int(schema.get("minimum", 1))
    if schema_type == "boolean":
        return False
    if schema_type == "string":
        if field_name == "candidate_id" and schema.get("pattern"):
            return "CAND_SYNTHETIC_MANUAL_BUNDLE_V1"
        return f"{field_name.upper() or 'SYNTHETIC'}_V1"
    raise AssertionError(f"unsupported schema fixture type: {schema}")


def _manual_result_from_bundle(task_dir: Path) -> dict:
    handoff = json.loads((task_dir / "RESEARCH_ORCHESTRATOR_AI_HANDOFF.json").read_text(encoding="utf-8"))
    output_contract = json.loads((task_dir / "OUTPUT_CONTRACT.json").read_text(encoding="utf-8"))
    batch_schema = json.loads((task_dir / output_contract["schema_refs"]["batch_result"]).read_text(encoding="utf-8"))
    durable_schema = json.loads((task_dir / output_contract["schema_refs"]["durable_contract"]).read_text(encoding="utf-8"))
    candidate_id = "CAND_SYNTHETIC_MANUAL_BUNDLE_V1"
    contract: dict = {field: _schema_fixture(durable_schema["properties"][field], field) for field in durable_schema["required"]}
    semantic_properties = durable_schema["properties"]["full_semantic_record"]["properties"]
    full_record = {
        section: _schema_fixture(semantic_properties[section], section)
        for section in durable_schema["properties"]["full_semantic_record"]["required"]
    }
    full_record["candidate"]["candidate_id"] = candidate_id
    full_record["candidate"]["parent_hypothesis_id"] = "HYPOTHESIS_MANUAL_BUNDLE_V1"
    full_record["candidate"]["strategy_family"] = "DAILY_FACTOR"
    full_record["signal_predicate"]["source_hypothesis_id"] = "HYPOTHESIS_MANUAL_BUNDLE_V1"
    full_record["signal_predicate"]["source_candidate_id"] = candidate_id
    full_record["exit_predicate"]["source_hypothesis_id"] = "HYPOTHESIS_MANUAL_BUNDLE_V1"
    full_record["exit_predicate"]["source_candidate_id"] = candidate_id
    contract.update({
        "candidate_id": candidate_id,
        "candidate_hash": "PLACEHOLDER_CANDIDATE_HASH",
        "hypothesis_id": "HYPOTHESIS_MANUAL_BUNDLE_V1",
        "hypothesis_fingerprint": "HYPOTHESIS_FINGERPRINT_MANUAL_BUNDLE_V1",
        "semantic_fingerprint": "PLACEHOLDER_SEMANTIC_FINGERPRINT",
        "full_semantic_record": full_record,
        "family": "DAILY_FACTOR",
        "mechanism": "synthetic_new",
        "factor_ids": ["SYNTHETIC_FACTOR"],
        "factor_roles": [{"factor_id": "SYNTHETIC_FACTOR", "role": "SIGNAL", "direction": "POSITIVE"}],
        "factor_directions": {"SYNTHETIC_FACTOR": "POSITIVE"},
        "entry_predicate": {"type": "FACTOR_THRESHOLD", "factor_id": "SYNTHETIC_FACTOR"},
        "interaction_semantics": {"eligibility_conditions": [], "interaction_conditions": []},
        "selection_rule": {"type": "TOP_N", "top_n": 1},
        "entry_timing": {"type": "NEXT_SESSION_OPEN"},
        "exit_contract": {"type": "FIXED_HOLD", "sessions": 5},
        "t_plus_1_contract": {"enabled": True},
        "capital_product_contract_identity": {"contract_id": "SYNTHETIC_CAPITAL_V1"},
        "fee_slippage_contract_references": {"contract_id": "SYNTHETIC_COST_V1"},
        "pit_dependencies": {"universe_rule": {"market": ["SH", "SZ"]}, "pit_required": True},
        "factor_event_registry_identities": {"factor_registry": "SYNTHETIC_FACTOR_REGISTRY_V1"},
        "research_period_identity": {"period_id": "SYNTHETIC_RESEARCH_PERIOD_V1"},
        "source_provenance": {"handoff_id": handoff["handoff_id"]},
        "holding_period_trading_sessions": 5,
        "execution_contract_version": "SYNTHETIC_EXECUTION_V1",
        "policy_identity": {"objective_id": handoff["objective_id"]},
        "created_frozen_timestamp": handoff["created_at"],
        "contract_schema_version": "durable-frozen-candidate-contract-v1",
    })
    contract.pop("content_hash")
    draft_path = task_dir / "draft_contract.json"
    draft_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    helper = task_dir / output_contract["contract_hash_helper"]
    completed = subprocess.run(
        [sys.executable, helper.name, draft_path.name],
        cwd=task_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    contract = json.loads(draft_path.read_text(encoding="utf-8"))
    assert completed.stdout.strip() == contract["content_hash"]
    assert set(contract) == set(durable_schema["required"])
    candidate_hash = contract["candidate_hash"]
    semantic_fingerprint = contract["semantic_fingerprint"]
    candidate_hashes = dict(output_contract["candidate_hashes_example"])
    candidate_hashes.clear()
    candidate_hashes[candidate_id] = candidate_hash
    result = {
        "schema_version": batch_schema["properties"]["schema_version"]["const"],
        "handoff_id": output_contract["handoff_id"],
        "objective_id": output_contract["objective_id"],
        "ai_invocation_id": output_contract["ai_invocation_id"],
        "candidate_ids": [candidate_id],
        "candidate_hashes": candidate_hashes,
        "candidates": [{"candidate_id": candidate_id, "candidate_hash": candidate_hash, "family_id": "synthetic_new", "mechanism": "synthetic_new", "factor_ids": ["SYNTHETIC_FACTOR"], "event_ids": [], "holding_period_days": 5}],
        "candidate_contracts": [contract],
        "durable_contract_refs": ["inline:candidate_contracts[0]"],
        "artifact_refs": [],
        "validation_status": "VALID",
        "no_outcome_compliance_status": "PASS",
    }
    assert set(result) == set(batch_schema["required"])
    validate_json_schema(result, batch_schema)
    return result


def test_validator_rejects_present_but_empty_executable_contract_semantics(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_EMPTY_CONTRACT_OBJECTIVE", budget_total=1)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime)
    waiting = orchestrator.run()
    task_dir = tmp_path / waiting["manual_handoff"]["task_dir"]
    manifest = _manual_result_from_bundle(task_dir)
    manifest["candidate_contracts"][0]["entry_predicate"] = {}

    with pytest.raises(AIBatchValidationError, match="AI_BATCH_DURABLE_CONTRACT_SEMANTICS_INCOMPLETE"):
        AIBatchValidatorV2(tmp_path).validate(manifest, waiting["current_handoff"], runtime.snapshot())


def test_validator_rejects_contract_that_cannot_be_executed_by_structural_provider():
    root = Path(__file__).resolve().parents[2]
    contract = next(
        item
        for registry_path in sorted((root / "data/research/research_factory/batches").glob("*/durable_frozen_candidate_contracts.json"))
        for item in json.loads(registry_path.read_text(encoding="utf-8"))["contracts"]
        if item["family"] in {"DAILY_EVENT", "DAILY_CROSS_SECTIONAL", "DAILY_FACTOR"}
        and DurableFrozenCandidateContractV1.from_dict(item).provider_candidate_payload()
    )
    objective_id = contract["policy_identity"]["objective_id"]
    contract["family"] = "CORRECTED_V3_HISTORICAL"
    contract["policy_identity"] = {"objective_id": objective_id}
    contract["content_hash"] = stable_hash({key: value for key, value in contract.items() if key != "content_hash"})
    candidate_id = contract["candidate_id"]
    candidate_hash = contract["candidate_hash"]
    candidate = _candidate(candidate_id, mechanism=contract["mechanism"], candidate_hash=candidate_hash)
    manifest = {
        "schema_version": "ai-research-batch-result-v2",
        "handoff_id": "HANDOFF_PROVIDER_COMPATIBILITY",
        "objective_id": objective_id,
        "ai_invocation_id": "INVOCATION_PROVIDER_COMPATIBILITY",
        "candidate_ids": [candidate_id],
        "candidate_hashes": {candidate_id: candidate_hash},
        "candidates": [candidate],
        "candidate_contracts": [contract],
        "durable_contract_refs": ["inline:candidate_contracts[0]"],
        "artifact_refs": [],
        "validation_status": "VALID",
        "no_outcome_compliance_status": "PASS",
    }
    handoff = {"handoff_id": manifest["handoff_id"], "search_space_context": {"mechanism_scope": [contract["mechanism"]]}}
    snapshot = CanonicalResearchSnapshotV2(
        objective_id=objective_id,
        objective_hash="OBJECTIVE_HASH",
        daemon_state="NEED_AI_RESEARCH_DESIGN",
        daemon_run_id=None,
        budget={"remaining": 1},
        candidates=(),
    )

    with pytest.raises(AIBatchValidationError, match="AI_BATCH_DURABLE_CONTRACT_PROVIDER_INCOMPATIBLE"):
        AIBatchValidatorV2(root).validate(manifest, handoff, snapshot)


def test_one_shot_completed_candidate_cannot_offer_misleading_recovery(tmp_path, monkeypatch):
    objective_id = "OBJECTIVE_ONE_SHOT_COMPLETE"

    class FakeStatus:
        def to_dict(self):
            return {
                "orchestrator_state": OrchestratorState.AI_HANDOFF_BLOCKED.value,
                "orchestrator_state_zh": "AI 交接已封锁",
                "terminal_reason": "READY_FOR_NEXT_CANDIDATE",
                "terminal_reason_zh": "等待下一个候选策略",
                "current_candidate": None,
                "last_ai_invocation": {"status": "ACCEPTED"},
                "closeout_complete": False,
            }

    class FakeOrchestrator:
        def __init__(self):
            self.root = tmp_path
            self.objective_id = objective_id

        def status(self):
            return FakeStatus()

    service = OrchestratorControlServiceV1(tmp_path)
    monkeypatch.setattr(service, "_orchestrator", lambda _objective_id: FakeOrchestrator())
    monkeypatch.setattr(service, "_process_running", lambda _orchestrator: False)
    monkeypatch.setattr("chanlun_trader.research_factory.autonomous_orchestrator_v2.is_one_shot_followup", lambda _root, _objective_id: True)

    actions = {item["action"]: item for item in service.operations(objective_id)["actions"]}

    assert actions["RECOVER"]["available"] is False
    assert "不会重跑" in actions["RECOVER"]["description_zh"]


def test_invalidated_one_shot_candidate_enters_governance_without_second_handoff(tmp_path, monkeypatch):
    objective_id = "OBJECTIVE_ONE_SHOT_INVALIDATED"
    runtime = SyntheticAutonomousResearchRuntimeV2(objective_id, budget_total=4)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=objective_id, runtime=runtime)
    orchestrator.checkpoint["state"] = OrchestratorState.ACTIVE.value
    orchestrator.store.atomic_write(orchestrator.store.invocation_path, {
        "ai_invocation_id": "AI_ONE_SHOT_ACCEPTED",
        "status": "ACCEPTED",
        "ingestion_status": "COMPLETED",
    })
    monkeypatch.setattr("chanlun_trader.research_factory.autonomous_orchestrator_v2.is_one_shot_followup", lambda _root, _objective_id: True)
    monkeypatch.setattr(
        "chanlun_trader.research_factory.autonomous_orchestrator_v2.load_effective_contract_invalidations",
        lambda _root, _objective_id: {"CANDIDATE_INVALID": {"reason_code": "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE"}},
    )
    monkeypatch.setattr(orchestrator, "_handoff", lambda _snapshot: pytest.fail("ONE_SHOT must not create a second AI handoff"))

    result = orchestrator.step()

    assert result["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert result["terminal_reason"] == "AI_ONE_SHOT_CANDIDATE_INVALIDATED"
    assert orchestrator.checkpoint["current_handoff_id"] is None
    assert orchestrator.checkpoint["current_ai_invocation_id"] is None
    assert result["current_handoff_id"] is None
    assert result["current_ai_invocation_id"] is None
    assert result["governance_decision_state"] == "REQUIRED"
    governance = json.loads(orchestrator.store.governance_path.read_text(encoding="utf-8"))
    assert governance["reason"] == "AI_ONE_SHOT_CANDIDATE_INVALIDATED"
    assert {item["choice"] for item in governance["allowed_choices"]} == {
        "STOP_RESEARCH",
        "START_PROMISING_FOLLOWUP_OBJECTIVE",
    }
    assert governance["current_terminal_summary"]["new_ai_handoffs"] == 0
    assert governance["current_terminal_summary"]["new_predictive_trials"] == 0


def test_consumed_one_shot_in_invocation_history_blocks_second_handoff(tmp_path, monkeypatch):
    objective_id = "OBJECTIVE_ONE_SHOT_HISTORY"
    runtime = SyntheticAutonomousResearchRuntimeV2(objective_id, budget_total=4)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=objective_id, runtime=runtime)
    orchestrator.checkpoint["state"] = OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED.value
    orchestrator.store.atomic_write(orchestrator.store.run_dir / "invocation_history" / "AI_ACCEPTED.json", {
        "ai_invocation_id": "AI_ACCEPTED",
        "handoff_id": "HANDOFF_ACCEPTED",
        "status": "ACCEPTED",
        "ingestion_status": "COMPLETED",
    })
    monkeypatch.setattr("chanlun_trader.research_factory.autonomous_orchestrator_v2.is_one_shot_followup", lambda _root, _objective_id: True)
    monkeypatch.setattr(orchestrator, "_handoff", lambda _snapshot: pytest.fail("ONE_SHOT must not create a second AI handoff"))

    result = orchestrator.step()

    assert result["orchestrator_state"] == OrchestratorState.AI_HANDOFF_BLOCKED.value
    assert result["terminal_reason"] == "AI_ONE_SHOT_POLICY_EXHAUSTED"
    assert result["current_handoff_id"] is None
    assert result["current_ai_invocation_id"] is None


@pytest.mark.parametrize("trial_status", ["COMPLETED", "BLOCKED"])
def test_recover_consumed_one_shot_terminal_trial_enters_closeout_without_replay(tmp_path, monkeypatch, trial_status):
    objective_id = "OBJECTIVE_ONE_SHOT_RECOVER_CLOSEOUT"
    candidate = _candidate("ONE_SHOT_CANONICAL_CANDIDATE")
    runtime = SyntheticAutonomousResearchRuntimeV2(objective_id, budget_total=4, initial_candidates=[candidate])
    runtime.trials.append({
        "trial_id": "CANONICAL_TERMINAL_TRIAL",
        "candidate_id": candidate["candidate_id"],
        "candidate_hash": candidate["candidate_hash"],
        "status": trial_status,
        "classification": "BLOCKED",
        "performance_accessed": True,
    })
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=objective_id, runtime=runtime, config=_config())
    orchestrator.checkpoint["state"] = OrchestratorState.AI_HANDOFF_BLOCKED.value
    orchestrator.checkpoint["terminal_reason"] = "AI_ONE_SHOT_POLICY_EXHAUSTED"
    orchestrator.store.atomic_write(orchestrator.store.invocation_path, {
        "ai_invocation_id": "AI_ONE_SHOT_ACCEPTED",
        "handoff_id": "HANDOFF_ONE_SHOT_ACCEPTED",
        "status": "ACCEPTED",
        "ingestion_status": "COMPLETED",
    })
    monkeypatch.setattr("chanlun_trader.research_factory.autonomous_orchestrator_v2.is_one_shot_followup", lambda _root, _objective_id: True)
    monkeypatch.setattr(orchestrator, "_handoff", lambda _snapshot: pytest.fail("ONE_SHOT must not create a second AI handoff"))

    result = orchestrator.recover()

    assert result["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert result["closeout_complete"] is True
    assert result["closeout_state"] == "COMPLETE"
    assert runtime.ai_batches == 0
    assert len(runtime.trials) == 1
    assert runtime.snapshot().budget["used"] == 1
    assert runtime.snapshot().budget["remaining"] == 3
    closeout = json.loads(orchestrator.store.closeout_path.read_text(encoding="utf-8"))
    assert closeout["terminal_reason"] == "AI_ONE_SHOT_POLICY_EXHAUSTED"
    assert closeout["budget_reconciliation"] == {
        "source": None,
        "used": 1,
        "total": 4,
        "remaining": 3,
        "reserved": 0,
        "delta": 0,
        "mutation_performed": False,
    }
    governance = json.loads(orchestrator.store.governance_path.read_text(encoding="utf-8"))
    assert governance["reason"] == "AI_ONE_SHOT_POLICY_EXHAUSTED"

    repeated = orchestrator.recover()

    assert repeated["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert repeated["closeout_complete"] is True
    closeout = json.loads(orchestrator.store.closeout_path.read_text(encoding="utf-8"))
    assert closeout["closeout_id"] == orchestrator.checkpoint["closeout_id"]
    assert closeout["budget_reconciliation"]["mutation_performed"] is False
    assert closeout["exact_once_audit"]["new_trial_entries"] == 0
    assert closeout["exact_once_audit"]["new_performance_access"] == 0
    assert runtime.ai_batches == 0
    assert len(runtime.trials) == 1
    assert runtime.snapshot().budget["used"] == 1


def test_web_recover_migrates_legacy_active_one_shot_without_replay(tmp_path, monkeypatch):
    objective_id = "OBJECTIVE_ONE_SHOT_LEGACY_ACTIVE"
    candidate = _candidate("ONE_SHOT_LEGACY_CANDIDATE")
    runtime = SyntheticAutonomousResearchRuntimeV2(objective_id, budget_total=4, initial_candidates=[candidate])
    runtime.trials.append({
        "trial_id": "CANONICAL_LEGACY_TERMINAL_TRIAL",
        "candidate_id": candidate["candidate_id"],
        "candidate_hash": candidate["candidate_hash"],
        "status": "COMPLETED",
        "classification": "BLOCKED",
        "performance_accessed": True,
    })
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=objective_id, runtime=runtime, config=_config())
    orchestrator.checkpoint["state"] = OrchestratorState.ACTIVE.value
    orchestrator.checkpoint["terminal_reason"] = "AI_ONE_SHOT_POLICY_EXHAUSTED"
    orchestrator.checkpoint["last_transition"] = {"reason_code": "RECOVERY_COMPLETE", "new_state": OrchestratorState.ACTIVE.value}
    orchestrator.store.atomic_write(orchestrator.store.invocation_path, {
        "ai_invocation_id": "AI_ONE_SHOT_LEGACY_ACCEPTED",
        "handoff_id": "HANDOFF_ONE_SHOT_LEGACY_ACCEPTED",
        "status": "ACCEPTED",
        "ingestion_status": "COMPLETED",
    })
    monkeypatch.setattr("chanlun_trader.research_factory.autonomous_orchestrator_v2.is_one_shot_followup", lambda _root, _objective_id: True)
    monkeypatch.setattr(orchestrator, "_handoff", lambda _snapshot: pytest.fail("legacy ONE_SHOT must not create a second AI handoff"))
    service = OrchestratorControlServiceV1(tmp_path)
    monkeypatch.setattr(service, "_orchestrator", lambda _objective_id: orchestrator)

    actions = {item["action"]: item for item in service.operations(objective_id)["actions"]}
    assert actions["RECOVER"]["available"] is True

    response = service.execute(objective_id, "RECOVER", idempotency_key="legacy-recover-1", confirmed=True)

    result = response["orchestrator"]
    assert result["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert result["closeout_complete"] is True
    closeout = json.loads(orchestrator.store.closeout_path.read_text(encoding="utf-8"))
    governance = json.loads(orchestrator.store.governance_path.read_text(encoding="utf-8"))
    assert closeout["terminal_reason"] == "AI_ONE_SHOT_POLICY_EXHAUSTED"
    assert governance["reason"] == "AI_ONE_SHOT_POLICY_EXHAUSTED"
    assert closeout["budget_reconciliation"]["used"] == 1
    assert closeout["budget_reconciliation"]["remaining"] == 3
    assert closeout["budget_reconciliation"]["mutation_performed"] is False
    assert closeout["exact_once_audit"]["new_trial_entries"] == 0
    assert closeout["exact_once_audit"]["new_performance_access"] == 0
    assert runtime.ai_batches == 0
    assert len(runtime.trials) == 1
    assert runtime.snapshot().budget["used"] == 1
    assert runtime.snapshot().budget["remaining"] == 3


def test_default_manual_handoff_is_compact_and_auto_resumes_after_valid_result(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_MANUAL_OBJECTIVE", budget_total=2, initial_candidates=[_candidate("LOCAL_001")])
    invoker = SyntheticCodexBatchInvokerV2()
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, ai_invoker=invoker)

    waiting = orchestrator.run()

    assert OrchestratorConfigV2().ai_invocation_mode == "MANUAL_HANDOFF"
    assert waiting["orchestrator_state"] == OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED.value
    assert waiting["ai_invocation_mode"] == "MANUAL_HANDOFF"
    assert waiting["background_ai_token_consumption"] == 0
    assert invoker.calls == 0
    task_dir = tmp_path / waiting["manual_handoff"]["task_dir"]
    assert (task_dir / "AI任务说明.md").exists()
    assert (task_dir / "AI研究提示词.md").exists()
    assert (task_dir / "NOOUTCOME_CONTEXT.json").exists()
    assert (task_dir / "OUTPUT_CONTRACT.json").exists()
    assert (task_dir / "AI_RESEARCH_BATCH_RESULT_V2.schema.json").exists()
    assert (task_dir / "DURABLE_FROZEN_CANDIDATE_CONTRACT_V1.schema.json").exists()
    assert (task_dir / "codex_contract_hash_helper.py").exists()
    prompt = (task_dir / "AI研究提示词.md").read_text(encoding="utf-8")
    assert len(prompt) < 2200
    assert 'candidate_hashes 必须是对象映射，例如 {"CANDIDATE_ID":"CANDIDATE_HASH"}' in prompt
    assert 'policy_identity 必须严格等于 {"objective_id":"SYNTHETIC_MANUAL_OBJECTIVE"}' in prompt
    output_contract = json.loads((task_dir / "OUTPUT_CONTRACT.json").read_text(encoding="utf-8"))
    assert output_contract["candidate_hashes_example"] == {"CANDIDATE_ID": "CANDIDATE_HASH"}
    durable_schema = json.loads((task_dir / "DURABLE_FROZEN_CANDIDATE_CONTRACT_V1.schema.json").read_text(encoding="utf-8"))
    semantic_schema = durable_schema["properties"]["full_semantic_record"]["properties"]
    assert set(StrategyCandidateSpec.__dataclass_fields__) <= set(semantic_schema["candidate"]["required"])
    assert set(SignalPredicateSpec.__dataclass_fields__) <= set(semantic_schema["signal_predicate"]["required"])
    assert set(ExitPredicateSpec.__dataclass_fields__) <= set(semantic_schema["exit_predicate"]["required"])
    assert "收益" not in (task_dir / "NOOUTCOME_CONTEXT.json").read_text(encoding="utf-8")

    result_path = tmp_path / waiting["manual_handoff"]["result_path"]
    result_path.write_text(json.dumps(_manual_result(waiting["current_handoff"], runtime.objective_id, waiting["current_ai_invocation_id"]), ensure_ascii=False), encoding="utf-8")
    accepted = orchestrator.scan_manual_handoff()

    assert accepted["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert runtime.ai_batches == 1
    assert runtime.local_calls == 2
    assert invoker.calls == 0
    assert accepted["background_ai_token_consumption"] == 0


def test_manual_handoff_new_mechanism_prompt_does_not_claim_promising_one_shot(tmp_path):
    objective_id = "SYNTHETIC_MANUAL_NEW_MECHANISM"
    objective_dir = tmp_path / "data/research/research_factory/objectives"
    objective_dir.mkdir(parents=True)
    (objective_dir / f"{objective_id}.json").write_text(
        json.dumps({
            "objective_id": objective_id,
            "lifecycle_state": "ACTIVE",
            "governance_action": "START_NEW_MECHANISM_OBJECTIVE",
            "mechanism_scope": ["LHB/flow event mechanisms"],
            "allowed_factor_scope": ["FACTOR_EXEC_A"],
            "risk_constraints": {"final_test_access": "DISABLED"},
        }),
        encoding="utf-8",
    )
    eligibility_path = tmp_path / f"reports/research_orchestrator_v2/{objective_id}/activation_eligibility.json"
    eligibility_path.parent.mkdir(parents=True, exist_ok=True)
    eligibility_path.write_text(json.dumps({"objective_id": objective_id, "activation_authorized": True, "activation_mode": "CREATE_AND_ACTIVATE", "target_state": "NEED_AI_RESEARCH_DESIGN"}), encoding="utf-8")
    runtime = SyntheticAutonomousResearchRuntimeV2(objective_id, budget_total=2)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=objective_id, runtime=runtime)

    waiting = orchestrator.run()
    task_dir = tmp_path / waiting["manual_handoff"]["task_dir"]
    prompt = (task_dir / "AI研究提示词.md").read_text(encoding="utf-8")
    ready = json.loads((task_dir / "HANDOFF_READY.json").read_text(encoding="utf-8"))

    assert ready["current_round"] == "NEW_MECHANISM"
    assert ready["task_purpose"] == "新机制研究设计"
    assert "当前研究类型是 NEW_MECHANISM，AI_DESIGN_POLICY=ITERATIVE" in prompt
    assert "PROMISING_FOLLOWUP，AI_DESIGN_POLICY=ONE_SHOT" not in prompt
    assert "新机制范围、合法因子能力" in prompt


def test_new_mechanism_manual_handoff_uses_new_mechanism_prompt_and_scope(tmp_path):
    objective_id = "SYNTHETIC_NEW_MECHANISM_OBJECTIVE"
    objective_path = tmp_path / f"data/research/research_factory/objectives/{objective_id}.json"
    objective_path.parent.mkdir(parents=True, exist_ok=True)
    objective_path.write_text(json.dumps({
        "objective_id": objective_id,
        "lifecycle_state": "ACTIVE",
        "governance_action": "START_NEW_MECHANISM_OBJECTIVE",
        "research_universe": ["SH", "SZ"],
        "holding_horizon": [2, 10],
        "preferred_horizon": [5, 8],
        "mechanism_scope": ["cross-sectional or sector-relative mechanisms"],
        "allowed_factor_scope": ["FACTOR_NEW_CROSS_SECTIONAL"],
        "risk_constraints": {"final_test_access": "DISABLED", "prospective_access": "DISABLED", "real_order_execution": "DISABLED"},
    }, ensure_ascii=False), encoding="utf-8")
    eligibility_path = tmp_path / f"reports/research_orchestrator_v2/{objective_id}/activation_eligibility.json"
    eligibility_path.parent.mkdir(parents=True, exist_ok=True)
    eligibility_path.write_text(json.dumps({"objective_id": objective_id, "activation_authorized": True, "activation_mode": "CREATE_AND_ACTIVATE", "target_state": "NEED_AI_RESEARCH_DESIGN"}), encoding="utf-8")
    runtime = SyntheticAutonomousResearchRuntimeV2(objective_id, budget_total=2)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=objective_id, runtime=runtime)

    waiting = orchestrator.run()
    task_dir = tmp_path / waiting["manual_handoff"]["task_dir"]
    handoff = json.loads((task_dir / "RESEARCH_ORCHESTRATOR_AI_HANDOFF.json").read_text(encoding="utf-8"))
    prompt = (task_dir / "AI研究提示词.md").read_text(encoding="utf-8")

    assert handoff["current_round"] == "NEW_MECHANISM"
    assert handoff["task_purpose"] == "新机制研究设计"
    assert handoff["ai_design_policy"]["current_round_mode"] == "ITERATIVE"
    assert "当前研究类型是 NEW_MECHANISM" in prompt
    assert "是否继续下一次设计由本地编排器" in prompt
    assert "当前研究类型是 PROMISING_FOLLOWUP" not in prompt
    assert "只生成一个候选，不生成变体" in prompt


def test_manual_handoff_contract_publishes_no_outcome_and_interaction_shape(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_MANUAL_CONTRACT_RULES", budget_total=1)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime)

    waiting = orchestrator.run()
    task_dir = tmp_path / waiting["manual_handoff"]["task_dir"]
    output_contract = json.loads((task_dir / "OUTPUT_CONTRACT.json").read_text(encoding="utf-8"))
    batch_schema = json.loads((task_dir / "AI_RESEARCH_BATCH_RESULT_V2.schema.json").read_text(encoding="utf-8"))
    durable_schema = json.loads((task_dir / "DURABLE_FROZEN_CANDIDATE_CONTRACT_V1.schema.json").read_text(encoding="utf-8"))

    assert "order" in output_contract["no_outcome_forbidden_fields"]
    assert output_contract["durable_contract_rules"]["interaction_semantics_allowed_fields"] == [
        "eligibility_conditions",
        "interaction_conditions",
    ]
    assert output_contract["durable_contract_rules"]["contract_schema_version"] == "durable-frozen-candidate-contract-v1"
    prompt = (task_dir / "AI研究提示词.md").read_text(encoding="utf-8")
    assert "包括把 order 作为说明性字段" in prompt
    assert "interaction_semantics 只能包含这两个字段" in prompt
    contract_schemas = (batch_schema["properties"]["candidate_contracts"]["items"], durable_schema)
    for schema in contract_schemas:
        interaction = schema["properties"]["interaction_semantics"]
        assert interaction["additionalProperties"] is False
        assert set(interaction["properties"]) == {"interaction_conditions", "eligibility_conditions"}
        assert schema["properties"]["contract_schema_version"]["const"] == "durable-frozen-candidate-contract-v1"


def test_manual_handoff_hash_helper_aligns_all_durable_identity_hashes(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_MANUAL_HASH_RULES", budget_total=1)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime)
    waiting = orchestrator.run()
    result = _manual_result_from_bundle(tmp_path / waiting["manual_handoff"]["task_dir"])
    contract = result["candidate_contracts"][0]
    record = contract["full_semantic_record"]
    candidate = record["candidate"]

    assert candidate["fingerprint"] == candidate["preregistration_hash"] == record["preregistration_hash"]
    assert contract["candidate_hash"] == record["preregistration_hash"]
    assert contract["semantic_fingerprint"] == record["semantic_fingerprint"]
    assert contract["semantic_fingerprint"] == stable_hash({"signal_predicate": record["signal_predicate"], "exit_predicate": record["exit_predicate"]})
    assert contract["content_hash"] == stable_hash({key: value for key, value in contract.items() if key != "content_hash"})
    assert result["candidate_hashes"] == {candidate["candidate_id"]: contract["candidate_hash"]}


def test_manual_handoff_schema_rejects_forbidden_interaction_shape(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_MANUAL_SCHEMA_RULES", budget_total=1)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime)
    waiting = orchestrator.run()
    task_dir = tmp_path / waiting["manual_handoff"]["task_dir"]
    result = _manual_result_from_bundle(task_dir)
    result["candidate_contracts"][0]["interaction_semantics"]["order"] = "event_gate_then_stock_confirmation"
    batch_schema = json.loads((task_dir / "AI_RESEARCH_BATCH_RESULT_V2.schema.json").read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        validate_json_schema(result, batch_schema)


def test_manual_no_outcome_error_reports_nested_field_path(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_MANUAL_NOOUTCOME_DIAGNOSTIC", budget_total=1)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime)
    waiting = orchestrator.run()
    manifest = _manual_result(waiting["current_handoff"], runtime.objective_id, waiting["current_ai_invocation_id"])
    manifest["candidate_contracts"][0]["interaction_semantics"] = {"order": "event_gate_then_stock_confirmation"}

    with pytest.raises(AIBatchValidationError, match=r"candidate_contracts\.0\.interaction_semantics\.order"):
        AIBatchValidatorV2(tmp_path).validate(manifest, waiting["current_handoff"], runtime.snapshot())


def test_manual_handoff_bundle_alone_builds_ingestable_durable_result(tmp_path):
    class ContractCheckingSyntheticRuntime(SyntheticAutonomousResearchRuntimeV2):
        def ingest_ai_batch(self, manifest_path, manifest, *, staging_dir=None):
            for contract in manifest["candidate_contracts"]:
                DurableFrozenCandidateContractV1.from_dict(contract)
            return super().ingest_ai_batch(manifest_path, manifest, staging_dir=staging_dir)

    runtime = ContractCheckingSyntheticRuntime("SYNTHETIC_MANUAL_BUNDLE_OBJECTIVE", budget_total=2, initial_candidates=[_candidate("LOCAL_001")])
    invoker = SyntheticCodexBatchInvokerV2()
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, ai_invoker=invoker)
    waiting = orchestrator.run()
    task_dir = tmp_path / waiting["manual_handoff"]["task_dir"]
    result = _manual_result_from_bundle(task_dir)
    (task_dir / "AI_RESEARCH_BATCH_RESULT_V2.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    accepted = orchestrator.scan_manual_handoff()

    assert accepted["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert runtime.ai_batches == 1
    assert "CAND_SYNTHETIC_MANUAL_BUNDLE_V1" in runtime.candidates
    assert invoker.calls == 0


def test_invalid_manual_result_is_preserved_without_ingest_or_retry(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_INVALID_MANUAL_OBJECTIVE", budget_total=2, initial_candidates=[_candidate("LOCAL_001")])
    invoker = SyntheticCodexBatchInvokerV2()
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, ai_invoker=invoker)
    waiting = orchestrator.run()
    result_path = tmp_path / waiting["manual_handoff"]["result_path"]
    result_path.write_text("{not-json", encoding="utf-8")

    rejected = orchestrator.scan_manual_handoff()

    assert rejected["orchestrator_state"] == OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED.value
    assert rejected["last_ai_invocation"]["status"] == "INVALID"
    assert rejected["last_ai_invocation"]["retryable"] is False
    assert runtime.ai_batches == 0
    assert runtime.local_calls == 1
    assert invoker.calls == 0
    assert rejected["budget"]["used"] == waiting["budget"]["used"]


def test_stale_manual_result_is_rejected_without_budget_use(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_STALE_MANUAL_OBJECTIVE", budget_total=2, initial_candidates=[_candidate("LOCAL_001")])
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, ai_invoker=SyntheticCodexBatchInvokerV2())
    waiting = orchestrator.run()
    result_path = tmp_path / waiting["manual_handoff"]["result_path"]
    stale = _manual_result(waiting["current_handoff"], runtime.objective_id, waiting["current_ai_invocation_id"])
    stale["generated_at"] = "2000-01-01T00:00:00+00:00"
    result_path.write_text(json.dumps(stale), encoding="utf-8")

    rejected = orchestrator.scan_manual_handoff()

    assert rejected["last_ai_invocation"]["error_code"] == "AI_MANUAL_RESULT_STALE"
    assert rejected["orchestrator_state"] == OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED.value
    assert runtime.ai_batches == 0
    assert rejected["budget"]["used"] == waiting["budget"]["used"]


def test_governed_manual_result_rejects_stale_live_context_without_ingest_or_side_effects(tmp_path):
    from test_candidate_generation_governance_v1 import OBJECTIVE_ID, _prepare

    root = _prepare(tmp_path)
    objective_path = root / f"data/research/research_factory/objectives/{OBJECTIVE_ID}.json"
    objective_payload = json.loads(objective_path.read_text(encoding="utf-8"))
    objective_payload["lifecycle_state"] = "ACTIVE"
    objective_path.write_text(json.dumps(objective_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    runtime = SyntheticAutonomousResearchRuntimeV2(OBJECTIVE_ID, budget_total=2)
    invoker = SyntheticCodexBatchInvokerV2()
    orchestrator = AutonomousResearchOrchestratorV2(root, objective_id=OBJECTIVE_ID, runtime=runtime, ai_invoker=invoker)

    waiting = orchestrator.run()
    result_path = root / waiting["manual_handoff"]["result_path"]
    result_path.write_text(
        json.dumps(_manual_result(waiting["current_handoff"], OBJECTIVE_ID, waiting["current_ai_invocation_id"]), ensure_ascii=False),
        encoding="utf-8",
    )
    data_path = root / "data/research/data_capability.json"
    data_payload = json.loads(data_path.read_text(encoding="utf-8"))
    data_payload["datasets"][0]["data_version"] = "stale-after-handoff"
    data_path.write_text(json.dumps(data_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rejected = orchestrator.scan_manual_handoff()

    assert rejected["orchestrator_state"] == OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED.value
    assert rejected["last_ai_invocation"]["status"] == "INVALID"
    assert rejected["last_ai_invocation"]["error_code"] == "STALE_AI_HANDOFF_CONTEXT"
    assert runtime.ai_batches == 0
    assert runtime.local_calls == 0
    assert runtime.candidates == {}
    assert runtime.trials == []
    assert runtime.snapshot().budget["used"] == 0
    assert not list(root.rglob("durable_frozen_candidate_contracts.json"))
    assert not list(root.rglob("factory_trial_ledger.json"))
    assert invoker.calls == 0


def test_legacy_manual_handoff_compatibility_is_explicitly_version_gated(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_LEGACY_HANDOFF_OBJECTIVE", budget_total=1)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime)
    legacy = {
        "schema_version": "research-orchestrator-ai-handoff-v2",
        "context_compatibility": MANUAL_HANDOFF_LEGACY_CONTEXT_COMPATIBILITY,
    }
    orchestrator._validate_manual_handoff_live_context(legacy)

    with pytest.raises(AIBatchValidationError, match="STALE_AI_HANDOFF_CONTEXT"):
        orchestrator._validate_manual_handoff_live_context({"schema_version": "research-orchestrator-ai-handoff-v2"})


def test_malformed_manual_result_exits_validation_with_contract_error(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_MALFORMED_MANUAL_OBJECTIVE", budget_total=2, initial_candidates=[_candidate("LOCAL_001")])
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime)
    waiting = orchestrator.run()
    initial_budget_used = waiting["budget"]["used"]
    result_path = tmp_path / waiting["manual_handoff"]["result_path"]
    malformed = _manual_result(waiting["current_handoff"], runtime.objective_id, waiting["current_ai_invocation_id"])
    malformed["candidate_hashes"] = [malformed["candidates"][0]["candidate_hash"]]
    result_path.write_text(json.dumps(malformed), encoding="utf-8")

    rejected = orchestrator.scan_manual_handoff()

    record = rejected["last_ai_invocation"]
    assert rejected["orchestrator_state"] == OrchestratorState.AI_MANUAL_HANDOFF_REQUIRED.value
    assert record["status"] == "INVALID"
    assert record["validation_status"] == "INVALID"
    assert record["error_code"] == "AI_MANUAL_RESULT_INVALID:AI_BATCH_CANDIDATE_HASHES_INVALID"
    assert record["validation_stage"] == "FAILED"
    assert record["validation_started_at"]
    assert record["validation_last_activity_at"]
    assert record["validation_finished_at"]
    assert "候选哈希字段格式" in record["error_message_zh"]
    assert runtime.ai_batches == 0
    assert rejected["budget"]["used"] == initial_budget_used


def test_validator_exception_becomes_engineering_blocked(tmp_path, monkeypatch):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_VALIDATOR_EXCEPTION_OBJECTIVE", budget_total=2, initial_candidates=[_candidate("LOCAL_001")])
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime)
    waiting = orchestrator.run()
    initial_budget_used = waiting["budget"]["used"]
    result_path = tmp_path / waiting["manual_handoff"]["result_path"]
    result_path.write_text(json.dumps(_manual_result(waiting["current_handoff"], runtime.objective_id, waiting["current_ai_invocation_id"])), encoding="utf-8")

    def explode(*_args, **_kwargs):
        raise ValueError("validator boom")

    monkeypatch.setattr("chanlun_trader.research_factory.autonomous_orchestrator_v2.AIBatchValidatorV2.validate", explode)

    failed = orchestrator.scan_manual_handoff()

    record = failed["last_ai_invocation"]
    assert failed["orchestrator_state"] == OrchestratorState.ENGINEERING_BLOCKED.value
    assert record["status"] == "ENGINEERING_BLOCKED"
    assert record["validation_status"] == "ERROR"
    assert record["validation_stage"] == "FAILED"
    assert record["error_code"] == "AI_MANUAL_RESULT_VALIDATION_ENGINEERING_ERROR:ValueError"
    assert "工程异常" in record["error_message_zh"]
    assert runtime.ai_batches == 0
    assert failed["budget"]["used"] == initial_budget_used


def test_repeated_scan_of_invalid_manual_result_is_idempotent(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_REPEAT_INVALID_MANUAL_OBJECTIVE", budget_total=2, initial_candidates=[_candidate("LOCAL_001")])
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime)
    waiting = orchestrator.run()
    initial_budget_used = waiting["budget"]["used"]
    result_path = tmp_path / waiting["manual_handoff"]["result_path"]
    malformed = _manual_result(waiting["current_handoff"], runtime.objective_id, waiting["current_ai_invocation_id"])
    malformed["candidate_hashes"] = [malformed["candidates"][0]["candidate_hash"]]
    result_path.write_text(json.dumps(malformed), encoding="utf-8")

    first = orchestrator.scan_manual_handoff()
    second = orchestrator.scan_manual_handoff()

    assert first["last_ai_invocation"]["status"] == "INVALID"
    assert second["last_ai_invocation"]["status"] == "INVALID"
    assert runtime.ai_batches == 0
    assert second["budget"]["used"] == initial_budget_used


def test_headless_rescan_recovers_from_validation_checkpoint(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_VALIDATION_RECOVERY_OBJECTIVE", budget_total=1)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime)
    waiting = orchestrator.run()
    result_path = tmp_path / waiting["manual_handoff"]["result_path"]
    result_path.write_text(json.dumps(_manual_result(waiting["current_handoff"], runtime.objective_id, waiting["current_ai_invocation_id"])), encoding="utf-8")
    orchestrator._set_state(OrchestratorState.AI_OUTPUT_VALIDATING, "TEST_STALE_VALIDATION_CHECKPOINT")

    recovered = orchestrator.scan_manual_handoff()

    assert recovered["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert recovered["last_ai_invocation"]["validation_status"] == "PASS"
    assert recovered["last_ai_invocation"]["validation_stage"] == "COMPLETED"
    assert runtime.ai_batches == 1


def test_ai_disabled_creates_no_manual_task_and_does_not_invoke_codex(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_DISABLED_OBJECTIVE", budget_total=1)
    invoker = SyntheticCodexBatchInvokerV2()
    result = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, ai_invoker=invoker, config=_config(ai_invocation_mode="AI_DISABLED")).run()

    assert result["orchestrator_state"] == OrchestratorState.AI_RESEARCH_DISABLED.value
    assert result["ai_invocation_mode"] == "AI_DISABLED"
    assert result["current_handoff_id"] is None
    assert invoker.calls == 0
    assert not (tmp_path / "research_ai_staging").exists()


def test_consumed_ai_batch_opens_next_handoff_when_budget_remains(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=2)
    invoker = SyntheticCodexBatchInvokerV2()

    result = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=invoker,
        config=_config(),
    ).run()
    events = [json.loads(line) for line in (tmp_path / "reports" / "research_orchestrator_v2" / runtime.objective_id / "orchestrator_events.jsonl").read_text(encoding="utf-8").splitlines()]

    assert result["orchestrator_state"] == OrchestratorState.AI_HANDOFF_BLOCKED.value
    assert invoker.calls == 3
    assert any(event["event_type"] == "AI_BATCH_CONSUMED_NEXT_HANDOFF" for event in events)


def test_budget_exhaustion_never_invokes_ai_and_preserves_unevaluated(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2(
        "SYNTHETIC_OBJECTIVE",
        budget_total=1,
        initial_candidates=[_candidate("LOCAL_001"), _candidate("LOCAL_002")],
    )
    invoker = SyntheticCodexBatchInvokerV2()

    result = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=invoker,
        config=_config(),
    ).run()
    closeout = json.loads((tmp_path / "reports" / "CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.json").read_text(encoding="utf-8"))

    assert invoker.calls == 0
    assert result["budget"]["used"] == 1
    assert closeout["unevaluated_candidates"]["classification"] == "UNEVALUATED_DUE_TO_BUDGET_EXHAUSTION"
    assert closeout["unevaluated_candidates"]["count"] == 1
    assert closeout["unevaluated_candidates"]["failure_knowledge_excluded"] is True


def test_closeout_is_idempotent_and_promising_is_not_promoted(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2(
        "SYNTHETIC_OBJECTIVE",
        budget_total=1,
        initial_candidates=[_candidate("LOCAL_001")],
        outcomes=("PROMISING",),
    )
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, config=_config())
    orchestrator.run()
    path = tmp_path / "reports" / "CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.json"
    first_bytes = path.read_bytes()
    first = json.loads(first_bytes)
    orchestrator.run()
    second = json.loads(path.read_text(encoding="utf-8"))

    assert path.read_bytes() == first_bytes
    assert first["promising_summary"]["count"] == 1
    assert first["promising_summary"]["research_passed_count"] == 0
    assert second["closeout_id"] == first["closeout_id"]


def test_stale_checkpoint_closeout_reference_reconciles_without_rewriting_closeout(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=0, initial_candidates=[_candidate("UNTESTED")])
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, config=_config())
    orchestrator.run()

    closeout_path = tmp_path / "reports" / "CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.json"
    checkpoint_path = tmp_path / "reports" / "research_orchestrator_v2" / runtime.objective_id / "orchestrator_checkpoint.json"
    events_path = tmp_path / "reports" / "research_orchestrator_v2" / runtime.objective_id / "orchestrator_events.jsonl"
    closeout_bytes = closeout_path.read_bytes()
    closeout_id = json.loads(closeout_bytes)["closeout_id"]
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["closeout_id"] = "CLOSEOUT_V2_STALE_REFERENCE"
    checkpoint["last_transition"]["details"]["closeout_id"] = "CLOSEOUT_V2_STALE_REFERENCE"
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")

    result = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, config=_config()).reconcile_closeout_reference()
    reconciled_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))

    assert result["status"] == "PASS"
    assert result["closeout_id"] == closeout_id
    assert closeout_path.read_bytes() == closeout_bytes
    assert reconciled_checkpoint["closeout_id"] == closeout_id
    assert reconciled_checkpoint["last_transition"]["reason_code"] == "CLOSEOUT_REFERENCE_RECONCILED"
    assert reconciled_checkpoint["last_transition"]["details"]["semantic_closeout_reused"] is True
    assert any(json.loads(line)["event_type"] == "CLOSEOUT_REFERENCE_RECONCILED" for line in events_path.read_text(encoding="utf-8").splitlines())


def test_invalid_ai_output_retries_then_blocks_without_budget_use(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=1)

    def invalid(_handoff, _invocation_id):
        return {"schema_version": "wrong"}

    invoker = SyntheticCodexBatchInvokerV2(invalid)
    result = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=invoker,
        config=_config(max_attempts_per_handoff=2),
    ).run()
    invocation = json.loads((tmp_path / "reports" / "research_orchestrator_v2" / runtime.objective_id / "ai_invocation.json").read_text(encoding="utf-8"))

    assert result["orchestrator_state"] == OrchestratorState.AI_HANDOFF_BLOCKED.value
    assert invoker.calls == 2
    assert invocation["attempt"] == 2
    assert runtime.snapshot().budget["used"] == 0


def test_unavailable_ai_fails_closed_without_retrying(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=1)

    def unavailable(_handoff, _invocation_id):
        from chanlun_trader.research_factory.autonomous_orchestrator_v2 import AIInvocationError

        raise AIInvocationError("AI_INVOCATION_UNAVAILABLE:CODEX_RUNTIME_NOT_AVAILABLE", retryable=False)

    invoker = SyntheticCodexBatchInvokerV2(unavailable)
    result = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=invoker,
        config=_config(),
    ).run()

    assert result["orchestrator_state"] == OrchestratorState.AI_INVOCATION_UNAVAILABLE.value
    assert invoker.calls == 1
    assert runtime.snapshot().budget["used"] == 0


def test_explicit_recover_from_ai_blocked_starts_new_handoff_generation(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=1)

    def unavailable(_handoff, _invocation_id):
        raise AIInvocationError("AI_INVOCATION_UNAVAILABLE:TEST", retryable=False)

    invoker = SyntheticCodexBatchInvokerV2(unavailable)
    orchestrator = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=invoker,
        config=_config(),
    )
    first = orchestrator.run()
    handoff_path = tmp_path / "reports" / "RESEARCH_ORCHESTRATOR_AI_HANDOFF_CURRENT.json"
    old_handoff = json.loads(handoff_path.read_text(encoding="utf-8"))

    recovered = orchestrator.recover()
    second = orchestrator.run()
    new_handoff = json.loads(handoff_path.read_text(encoding="utf-8"))

    assert first["orchestrator_state"] == OrchestratorState.AI_INVOCATION_UNAVAILABLE.value
    assert recovered["orchestrator_state"] == OrchestratorState.ACTIVE.value
    assert second["orchestrator_state"] == OrchestratorState.AI_INVOCATION_UNAVAILABLE.value
    assert new_handoff["handoff_id"] != old_handoff["handoff_id"]
    assert (tmp_path / "reports" / "research_orchestrator_v2" / runtime.objective_id / "handoff_history" / f"{old_handoff['handoff_id']}.json").exists()
    assert invoker.calls == 2


def test_accepted_ai_batch_replay_can_resume_local_research_from_pending(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=1)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, config=_config())
    orchestrator.checkpoint["state"] = OrchestratorState.AI_INVOCATION_PENDING.value
    assert orchestrator.status().to_dict()["ai_status"] == OrchestratorState.AI_INVOCATION_PENDING.value

    orchestrator._set_state(OrchestratorState.LOCAL_RESEARCH_RESUMING, "AI_BATCH_ACCEPTED_REPLAY")

    status = orchestrator.status().to_dict()
    assert status["orchestrator_state"] == OrchestratorState.LOCAL_RESEARCH_RESUMING.value
    assert status["ai_status"] == OrchestratorState.LOCAL_RESEARCH_RESUMING.value


def test_local_resume_no_progress_is_closed_as_a_controlled_state(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=1)
    orchestrator = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, config=_config())
    orchestrator.checkpoint["state"] = OrchestratorState.LOCAL_RESEARCH_RESUMING.value

    orchestrator._set_state(OrchestratorState.NO_PROGRESS_RESEARCH_LOOP, "NO_PROGRESS_RESEARCH_LOOP")

    assert orchestrator.status().to_dict()["orchestrator_state"] == OrchestratorState.NO_PROGRESS_RESEARCH_LOOP.value


def test_duplicate_ai_candidate_is_rejected_before_ingestion():
    existing = _candidate("EXISTING")
    snapshot = CanonicalResearchSnapshotV2(
        objective_id="OBJECTIVE",
        objective_hash="OBJECTIVE_HASH",
        daemon_state="NEED_AI_RESEARCH_DESIGN",
        daemon_run_id=None,
        budget={"remaining": 1},
        candidates=(existing,),
    )
    handoff = {"handoff_id": "HANDOFF", "search_space_context": {"mechanism_scope": []}}
    manifest = {
        "schema_version": "ai-research-batch-result-v2",
        "handoff_id": "HANDOFF",
        "objective_id": "OBJECTIVE",
        "ai_invocation_id": "INVOCATION",
        "candidate_ids": [existing["candidate_id"]],
        "candidate_hashes": {existing["candidate_id"]: existing["candidate_hash"]},
        "candidates": [existing],
        "durable_contract_refs": [],
        "artifact_refs": [],
        "validation_status": "VALID",
        "no_outcome_compliance_status": "PASS",
    }

    with pytest.raises(AIBatchValidationError, match="DUPLICATE"):
        AIBatchValidatorV2(".").validate(manifest, handoff, snapshot)


def test_crash_after_codex_completion_recovers_without_second_ai_call(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=1)
    invoker = SyntheticCodexBatchInvokerV2()
    crashing = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=invoker,
        config=_config(),
        crash_at="after_codex_completion",
    )
    with pytest.raises(RuntimeError, match="after_codex_completion"):
        crashing.run(max_steps=4)

    result = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=invoker,
        config=_config(),
    ).run()

    assert result["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert invoker.calls == 1
    assert runtime.ai_batches == 1
    assert runtime.local_calls == 1


def test_crash_after_ai_batch_ingest_reconciles_without_duplicate_batch(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=1)
    invoker = SyntheticCodexBatchInvokerV2()
    crashing = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=invoker,
        config=_config(),
        crash_at="after_ai_batch_ingest",
    )
    with pytest.raises(RuntimeError, match="after_ai_batch_ingest"):
        crashing.run(max_steps=4)

    result = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=invoker,
        config=_config(),
    ).run()

    assert result["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert invoker.calls == 1
    assert runtime.ai_batches == 1
    assert runtime.local_calls == 1


def test_crash_during_closeout_recovers_and_writes_governance(tmp_path):
    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=0, initial_candidates=[_candidate("UNTESTED")])
    crashing = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, config=_config(), crash_at="during_terminal_closeout")
    with pytest.raises(RuntimeError, match="during_terminal_closeout"):
        crashing.run(max_steps=3)

    result = AutonomousResearchOrchestratorV2(tmp_path, objective_id=runtime.objective_id, runtime=runtime, config=_config()).run()

    assert result["orchestrator_state"] == OrchestratorState.GOVERNANCE_DECISION_REQUIRED.value
    assert (tmp_path / "reports" / "CURRENT_OBJECTIVE_AUTOMATIC_CLOSEOUT_V2.json").exists()
    assert (tmp_path / "reports" / "RESEARCH_GOVERNANCE_DECISION_REQUIRED.json").exists()


def test_handoff_is_no_outcome_and_contains_explicit_context(tmp_path):
    captured = {}

    def capture(handoff, _invocation_id):
        captured.update(handoff)
        return SyntheticCodexBatchInvokerV2().invoke(handoff, invocation_id=_invocation_id, staging_dir=tmp_path, timeout_seconds=1)

    runtime = SyntheticAutonomousResearchRuntimeV2("SYNTHETIC_OBJECTIVE", budget_total=1)
    AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=runtime.objective_id,
        runtime=runtime,
        ai_invoker=SyntheticCodexBatchInvokerV2(capture),
        config=_config(),
    ).step()

    assert captured["reason"] == "NEED_AI_RESEARCH_DESIGN"
    assert captured["no_outcome_research_context"]["outcome_fields_available"] is False
    assert captured["performance_values_exposed"] is False
    assert "classification" not in json.dumps(captured, ensure_ascii=False).casefold()


def test_ready_objective_with_governance_activation_reaches_ai_design(tmp_path):
    objective_id = "RESEARCH_OBJECTIVE_GOVERNED_FOLLOWUP_TEST"
    (tmp_path / "data/research/research_factory/objectives").mkdir(parents=True)
    (tmp_path / f"data/research/research_factory/objectives/{objective_id}.json").write_text(
        json.dumps({"objective_id": objective_id, "lifecycle_state": "READY", "max_total_trials": 1, "risk_constraints": {"final_test_access": "DISABLED"}}),
        encoding="utf-8",
    )
    budget_path = tmp_path / f"data/research/research_factory/batches/{objective_id}_B01/search_budget_registry.json"
    budget_path.parent.mkdir(parents=True)
    budget_path.write_text(
        json.dumps({"schema_version": "search-budget-registry-v1", "objective_id": objective_id, "buckets": [{"kind": "objective", "key": objective_id, "limit": 1, "used": 0, "reserved": 0}], "active_reservations": {}, "settled_reservations": {}, "reservation_counter": 0, "updated_at": "2026-01-02T12:00:00+00:00"}),
        encoding="utf-8",
    )
    eligibility_path = tmp_path / f"reports/research_orchestrator_v2/{objective_id}/activation_eligibility.json"
    eligibility_path.parent.mkdir(parents=True)
    eligibility_path.write_text(
        json.dumps({"objective_id": objective_id, "eligibility": "READY", "activation_authorized": True, "activation_mode": "CREATE_AND_ACTIVATE", "target_state": "NEED_AI_RESEARCH_DESIGN"}),
        encoding="utf-8",
    )
    captured = {}

    class UnavailableInvoker:
        def invoke(self, handoff, *, invocation_id, staging_dir, timeout_seconds):
            captured.update(handoff)
            raise AIInvocationError("AI_INVOCATION_UNAVAILABLE:TEST", retryable=False)

    result = AutonomousResearchOrchestratorV2(
        tmp_path,
        objective_id=objective_id,
        runtime=CanonicalOrchestratorRuntimeV2(tmp_path, objective_id),
        ai_invoker=UnavailableInvoker(),
        config=_config(),
    ).step()

    assert result["orchestrator_state"] == OrchestratorState.AI_INVOCATION_UNAVAILABLE.value
    assert captured["reason"] == "NEED_AI_RESEARCH_DESIGN"
