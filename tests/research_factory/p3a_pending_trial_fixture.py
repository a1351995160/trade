"""P3-A 待恢复 Trial 合成工件；合同和政策均现场生成，不复制研究数据。"""
import json
from pathlib import Path
from chanlun_trader.research_daemon_state import DaemonCheckpointStoreV1, ResearchDaemonState
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractRegistryV1, DurableFrozenCandidateContractV1
from chanlun_trader.research.validation_policy_v2 import load_validation_decision_policy_v2
OBJECTIVE_ID = "SYNTHETIC_PREDICTIVE_TRIAL_START_V1"
BATCH_ID = "SYNTHETIC_PREDICTIVE_TRIAL_START_B01"
FAMILY_ID = "MTF_SYNTHETIC_PREDICTIVE_TRIAL_START_V1"
CONTRACT_REF = "data/research/research_factory/batches/SYNTHETIC_CONTRACT/durable_frozen_candidate_contracts.json"
RECONCILIATION_REF = f"reports/research_daemon/{OBJECTIVE_ID}/structural_preflight_reconciliation.json"
RECONCILIATION_ID = "SYNTHETIC_STRUCTURAL_RECONCILIATION_V1"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def pending_trial_fixture(root: Path) -> dict[str, Path]:
    from test_candidate_executable_materialization_v1 import _bridge_fixture
    _, _, generated_contract = _bridge_fixture(root / "seed")
    source_contract = generated_contract.to_dict()
    CANDIDATE_ID = generated_contract.candidate_id
    CANDIDATE_HASH = generated_contract.candidate_hash
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
    from chanlun_trader.research.validation_policy_v2 import default_validation_decision_policy_v2, lock_payload
    policy = default_validation_decision_policy_v2()
    _write_json(policy_path, policy.to_dict())
    _write_json(policy_path.with_suffix(".lock.json"), lock_payload(policy, policy_path))
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
