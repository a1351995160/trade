"""新协议合同验收；初始父目标是 fixture，新目标只由正式服务创建。"""
from copy import deepcopy
import hashlib
import json
import shutil
import os
from pathlib import Path
import subprocess
import sys

import pytest

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research.event import EventRegistry
from chanlun_trader.research.unified_factor import UnifiedFactorRegistry
from chanlun_trader.research.validation_policy_v2 import default_validation_decision_policy_v2, lock_payload
from chanlun_trader.research_factory.objective_execution_binding import ResearchProposalGovernanceServiceV2, POLICY_PATH
from chanlun_trader.research_factory.research_proposal_governance import ResearchProposalGovernanceError
from test_research_proposal_governance_v1 import _fixture, _confirm_payload, _write_json, PROPOSAL_ID, FIXED_NOW


def setup_binding(root, *, crash_at=None):
    fixture = _fixture(root)
    policy = default_validation_decision_policy_v2()
    path = _write_json(root, POLICY_PATH, policy.to_dict())
    _write_json(root, str(path.with_name("validation_decision_policy_v2.lock.json").relative_to(root)), lock_payload(policy, path))
    factors = UnifiedFactorRegistry().write(root / "data/research/factor_library_v1/registry.json")
    events = EventRegistry(root / "data/research/event_registry/registry.json").save()
    binding = {"policy_identity": {"policy_id": policy.policy_id, "version": policy.policy_version, "hash": policy.policy_hash},
        "research_period_identity": {"id": "SYNTHETIC_WINDOW", "start": 20240102, "end": 20240301},
        "factor_event_registry_identities": {key: {"path": file.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest()} for key, file in (("factor_registry", factors), ("event_registry", events))}}
    service = ResearchProposalGovernanceServiceV2(root, execution_policy=ExecutionPolicy("GOVERNED", "SYNTHETIC"),
        clock=lambda: FIXED_NOW, crash_at=crash_at)
    return fixture, service, binding


def approve(service, binding):
    return service.review(PROPOSAL_ID, "approve", "synthetic-test-driver", execution_binding=binding)["objective_creation_preview"]


def confirm(service, preview):
    return service.confirm(PROPOSAL_ID, {**_confirm_payload(preview, confirmer="synthetic-test-driver"), "test_confirmation": True})


def test_formal_creation_persists_binding_and_original_transaction(tmp_path):
    fixture, service, binding = setup_binding(tmp_path)
    parent_before = fixture["parent_budget_path"].read_bytes()
    preview = approve(service, binding)
    receipt = confirm(service, preview)
    objective = json.loads((tmp_path / "data/research/research_factory/objectives" / (receipt["objective_id"] + ".json")).read_text())
    assert objective["batch_id"] == objective["objective_id"] + "_B01"
    assert objective["policy_identity"] == {"objective_id": objective["objective_id"],
        "policy_id": binding["policy_identity"]["policy_id"], "policy_version": binding["policy_identity"]["version"],
        "policy_hash": binding["policy_identity"]["hash"]}
    assert objective["research_period_identity"] == binding["research_period_identity"]
    assert objective["factor_event_registry_identities"] == binding["factor_event_registry_identities"]
    family = json.loads((tmp_path / receipt["multiple_testing_family_ref"]).read_text())
    assert family["hypothesis_slots"] == 1
    assert family["immutable_after_confirmation"] is True
    assert (tmp_path / receipt["budget_registry_ref"]).is_file()
    assert fixture["parent_budget_path"].read_bytes() == parent_before
    assert receipt["trial_started"] is False
    assert receipt["schema_version"].endswith("-v2")
    assert confirm(service, preview)["idempotent"] is True
    assert service.recover(PROPOSAL_ID)["objective_id"] == objective["objective_id"]


@pytest.mark.parametrize("change", ["missing", "policy", "window", "registry", "schema"])
def test_invalid_binding_never_creates_objective_or_review(tmp_path, change):
    fixture, service, binding = setup_binding(tmp_path)
    before = fixture["proposal_path"].read_bytes()
    if change == "missing":
        binding.pop("policy_identity")
    elif change == "policy":
        binding["policy_identity"]["hash"] = "wrong"
    elif change == "window":
        binding["research_period_identity"]["end"] = 20260101
    elif change == "registry":
        (tmp_path / binding["factor_event_registry_identities"]["event_registry"]["path"]).unlink()
    else:
        identity = binding["factor_event_registry_identities"]["event_registry"]
        path = tmp_path / identity["path"]
        path.write_text("{}")
        identity["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        approve(service, binding)
    assert fixture["proposal_path"].read_bytes() == before
    assert not service._preview_path(PROPOSAL_ID).exists()


@pytest.mark.parametrize("source", ["policy", "lock", "factor_registry", "event_registry"])
def test_confirm_revalidates_actual_sources(tmp_path, source):
    _, service, binding = setup_binding(tmp_path)
    preview = approve(service, binding)
    path = tmp_path / POLICY_PATH
    if source == "lock":
        path = path.with_name("validation_decision_policy_v2.lock.json")
    elif source.endswith("registry"):
        path = tmp_path / binding["factor_event_registry_identities"][source]["path"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        confirm(service, preview)
    assert not service._receipt_path(PROPOSAL_ID).exists()


def test_version_and_explicit_test_confirmation_required(tmp_path):
    fixture, service, binding = setup_binding(tmp_path)
    preview = approve(service, binding)
    with pytest.raises(ResearchProposalGovernanceError, match="对应版本"):
        fixture["service"].confirm(PROPOSAL_ID, _confirm_payload(preview))
    with pytest.raises(ValueError, match="TEST_CONFIRMATION_REQUIRED"):
        service.confirm(PROPOSAL_ID, _confirm_payload(preview))
    changed = deepcopy(binding)
    changed["research_period_identity"]["id"] += "_changed"
    with pytest.raises(ValueError, match="BINDING_CHANGED"):
        approve(service, changed)
    confirm(service, preview)
    with pytest.raises(ResearchProposalGovernanceError, match="对应版本"):
        fixture["service"].recover(PROPOSAL_ID)


@pytest.mark.parametrize("checkpoint", ["after_objective_write", "after_budget_registry_write", "after_receipt_write"])
def test_confirmed_transaction_recovers_without_rebinding(tmp_path, checkpoint):
    _, service, binding = setup_binding(tmp_path, crash_at=checkpoint)
    preview = approve(service, binding)
    with pytest.raises(RuntimeError, match="SYNTHETIC_PROPOSAL_GOVERNANCE_CRASH"):
        confirm(service, preview)
    restarted = ResearchProposalGovernanceServiceV2(tmp_path, execution_policy=ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    receipt = restarted.recover(PROPOSAL_ID)
    assert receipt["objective_id"] == preview["target_objective_id"]
    assert receipt["trial_started"] is False


def test_copied_workspace_and_read_only_policy_reject(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    _, service, binding = setup_binding(root)
    preview = approve(service, binding)
    copied = tmp_path / "copied"
    shutil.copytree(root, copied)
    duplicate = ResearchProposalGovernanceServiceV2(copied, execution_policy=ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    with pytest.raises(ValueError, match="WORKSPACE_BINDING_CONFLICT"):
        confirm(duplicate, preview)
    with pytest.raises(PermissionError):
        ResearchProposalGovernanceServiceV2(root, execution_policy=ExecutionPolicy())


def test_public_versioned_entry_uses_actual_confirmation_service(tmp_path):
    from fastapi.testclient import TestClient
    from chanlun_trader.webapp import create_app
    _, _, binding = setup_binding(tmp_path)
    application = create_app(tmp_path, ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    route = f"/api/research/evolution/v2/proposals/{PROPOSAL_ID}"
    with TestClient(application) as client:
        reviewed = client.post(route + "/review", json={"action": "approve", "reviewer": "synthetic-test-driver", "execution_binding": binding})
        assert reviewed.status_code == 200, reviewed.text
        preview = reviewed.json()["objective_creation_preview"]
        assert client.post(route + "/confirm", json=_confirm_payload(preview)).status_code == 409
        response = client.post(route + "/confirm", json={**_confirm_payload(preview), "test_confirmation": True})
        assert response.status_code == 200, response.text
        assert response.json()["schema_version"].endswith("-v2")
        assert response.json()["trial_started"] is False


def test_real_process_lock_exit_recovery_and_replay(tmp_path):
    from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
    _, service, binding = setup_binding(tmp_path)
    preview = approve(service, binding)
    _write_json(tmp_path, "test-confirm-input.json", {**_confirm_payload(preview), "test_confirmation": True})
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / "objective-binding"
    output.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source / "tests/isolation"), str(source / "src")]))
    def run(action):
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("objective_binding_worker.py")),
                str(tmp_path), PROPOSAL_ID, action], cwd=tmp_path, env=environment, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            (output / (action + "-stdout.bin")).write_bytes(exc.stdout or b"")
            (output / (action + "-stderr.bin")).write_bytes(exc.stderr or b"")
            raise
        (output / (action + "-stdout.bin")).write_bytes(result.stdout)
        (output / (action + "-stderr.bin")).write_bytes(result.stderr)
        return result
    with ObjectiveMutationLock.for_resource(tmp_path / POLICY_PATH):
        blocked = run("confirm")
        assert blocked.returncode == 23, blocked.stderr.decode()
        assert not service._receipt_path(PROPOSAL_ID).exists()
    crashed = run("crash")
    assert crashed.returncode == 73, crashed.stderr.decode()
    assert not service._receipt_path(PROPOSAL_ID).exists()
    recovered = run("recover")
    assert recovered.returncode == 0, recovered.stderr.decode()
    receipt = json.loads(recovered.stdout)
    before = (tmp_path / receipt["budget_registry_ref"]).read_bytes()
    replay = run("replay")
    assert replay.returncode == 0, replay.stderr.decode()
    assert json.loads(replay.stdout)["idempotent"] is True
    assert (tmp_path / receipt["budget_registry_ref"]).read_bytes() == before


@pytest.mark.parametrize("recommendation", ["DISABLED", "ENABLED", {"performance": 99}])
def test_created_objective_enters_actual_design_freeze_materialization(tmp_path, recommendation):
    from p3c_scenario import Scenario
    from test_research_proposal_governance_v1 import OBJECTIVE_ID
    fixture, service, binding = setup_binding(tmp_path)
    # 只准备初始父研究的盲化上下文；目标由下方 confirm 创建，此后禁止补字段。
    parent = json.loads(fixture["objective_path"].read_bytes())
    parent["allowed_factor_scope"] = ["VOLUME_ACCEL"]
    _write_json(tmp_path, fixture["objective_path"].relative_to(tmp_path).as_posix(), parent)
    _write_json(tmp_path, f"reports/research_evolution/{OBJECTIVE_ID}/failure_landscape.json",
        {"schema_version": "research-failure-landscape-v1", "objective_id": OBJECTIVE_ID,
         "entries": [], "category_totals": {}, "read_only": True})
    _write_json(tmp_path, "reports/research_evolution/proposals/MECHANISM_COVERAGE_REGISTRY.json",
        {"schema_version": "mechanism-coverage-registry-v1", "coverage_id": "SYNTHETIC_COVERAGE",
         "covered": [], "unexplored": ["momentum"], "outcome_blind": True, "read_only": True})
    _write_json(tmp_path, "data/research/data_capability.json", {"datasets": [
        {"dataset_id": "daily_ohlcva_raw", "source": "SYNTHETIC_TEST_ONLY", "provider": "test-driver",
         "fields": ["date", "open", "high", "low", "close", "volume"], "frequency": "DAILY",
         "earliest_date": 20240102, "latest_date": 20240301, "PIT_safe": True, "status": "READY",
         "data_version": "synthetic-v1", "event_time_semantics": "TRADE_DATE", "available_at_semantics": "T_CLOSE"}]})
    preview = approve(service, binding)
    receipt = confirm(service, preview)
    objective_path = tmp_path / "data/research/research_factory/objectives" / (receipt["objective_id"] + ".json")
    before = objective_path.read_bytes()
    scenario = Scenario(tmp_path, receipt["objective_id"])
    scenario.design()
    scenario.approve()
    assert scenario.plane.tick(scenario.objective_id)["execution"]["execution_status"] == "COMPLETED"
    scenario.freeze()
    from chanlun_trader.research_factory.candidate_executable_materialization import CandidateExecutableMaterializationManagerV1, CandidateExecutableMaterializationError
    if recommendation != "DISABLED":
        corrupted = json.loads(before)
        corrupted["risk_constraints"]["recommendation"] = recommendation
        objective_path.write_text(json.dumps(corrupted), encoding="utf-8")
        with pytest.raises(CandidateExecutableMaterializationError, match="被禁止的结果字段"):
            CandidateExecutableMaterializationManagerV1(tmp_path).create_preview(scenario.objective_id)
        return
    CandidateExecutableMaterializationManagerV1(tmp_path).create_preview(scenario.objective_id)
    scenario.confirm()
    assert objective_path.read_bytes() == before
    assert scenario.proposal["durable_contract"]["policy_identity"]["objective_id"] == receipt["objective_id"]
