"""批次合同通过正式 Objective、冻结、物化和新颖性服务形成测试前置。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import pytest

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.synthetic_batch import SyntheticBatchServiceV1, SCHEMA
from chanlun_trader.research_factory.synthetic_novelty import SyntheticNoveltyBindingServiceV1


@pytest.fixture(scope="module")
def prepared_batch():
    return prepare_batch()


def prepare_batch(*, multiple=False):
    root = Path(tempfile.gettempdir()) / ("r3-contract-" + uuid.uuid4().hex)
    source = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source / "tests/isolation"), str(source / "src")]))
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / root.name
    output.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("r2_service_worker.py")), str(root), "prepare_multiple" if multiple else "prepare"],
            cwd=source, env=environment, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        (output / "prepare-stdout.bin").write_bytes(exc.stdout or b"")
        (output / "prepare-stderr.bin").write_bytes(exc.stderr or b"")
        raise
    (output / "prepare-stdout.bin").write_bytes(result.stdout)
    (output / "prepare-stderr.bin").write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr.decode()
    member = json.loads((root / "r3-prepared-candidate.json").read_bytes())
    members = [member]
    if multiple:
        members.append(json.loads((root / "r3-second-candidate.json").read_bytes()))
    policy = ExecutionPolicy("GOVERNED", "SYNTHETIC")
    novelty = SyntheticNoveltyBindingServiceV1(root)
    novelty.declare_sources(policy, [f"data/research/research_factory/batches/{item['objective_id']}_B01/durable_frozen_candidate_contracts.json" for item in members])
    for member in members:
        preview = novelty.preview(policy, member["candidate_id"], member["contract_hash"])
        novelty.confirm(policy, {"confirmed": True, "preview_id": preview["preview_id"]})
        member["novelty_confirmation"] = preview["preview_id"]
    now = datetime.now(timezone.utc)
    body = {"schema_version": SCHEMA, "candidates": members, "actions": ["RUN_STRUCTURAL_PREFLIGHT", "START_PREDICTIVE_TRIAL_1"],
        "model": "NONE", "effective_at": (now - timedelta(seconds=1)).isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(),
        "limits": {"candidates": len(members), "trials": len(members), "batches": len(members), "concurrency": 1, "retries": 0,
                   "model_calls": 0, "tokens": 0, "cost_minor_units": 0, "currency": "CNY", "wall_seconds": 120, "memory_mib": 2048}}
    return root, policy, body


def approval(preview):
    return {"confirmed": True, "test_confirmation": True, "preview_hash": preview["hash"]}


def test_relative_workspace_is_not_resolved_into_an_authorized_root():
    with pytest.raises(ValueError, match="INVALID_RESEARCH_ROOT"):
        SyntheticBatchServiceV1(Path("."), ExecutionPolicy("GOVERNED", "SYNTHETIC"))


def test_actual_preview_confirmation_and_control(prepared_batch):
    root, policy, body = prepared_batch
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    assert preview["execution_authorized"] is False
    with pytest.raises(ValueError, match="ACTUAL_TEST_CONFIRMATION"):
        service.confirm(identifier, {"confirmed": True, "preview_hash": preview["hash"]})
    confirmed = service.confirm(identifier, approval(preview))
    assert confirmed["execution_authorized"] is True
    assert confirmed["receipt"]["authorization_origin"] == "SYNTHETIC_TEST_HUMAN"
    assert confirmed["preview"]["bindings"][0]["budget_limits"]
    with pytest.raises(PermissionError, match="PARENT_AUTHORIZATION_NOT_ACTIVE"):
        with service.admission(identifier, "caller-invented-execution", action="START_PREDICTIVE_TRIAL_1"):
            pytest.fail("普通执行 ID 不能创造委托")
    assert service.control(identifier, "pause", approval(preview))["status"] == "PAUSED"
    assert service.control(identifier, "resume", approval(preview))["status"] == "ACTIVE"
    assert service.control(identifier, "revoke", approval(preview))["status"] == "REVOKED"
    assert SyntheticBatchServiceV1(root, policy).confirm(identifier, approval(preview))["status"] == "REVOKED"
    with pytest.raises(ValueError, match="CONTROL_STATE"):
        service.control(identifier, "resume", approval(preview))


@pytest.mark.parametrize("change", ["missing_limit", "parallel", "retry", "model", "unknown_action", "duplicate", "wrong_contract", "trial_cap"])
def test_invalid_request_does_not_form_preview(prepared_batch, change):
    root, policy, original = prepared_batch
    body = deepcopy(original)
    if change == "missing_limit":
        del body["limits"]["memory_mib"]
    elif change == "parallel":
        body["limits"]["concurrency"] = 2
    elif change == "retry":
        body["limits"]["retries"] = 1
    elif change == "model":
        body["model"] = "BUSINESS_MODEL"
    elif change == "unknown_action":
        body["actions"].append("FINAL_TEST")
    elif change == "duplicate":
        body["candidates"].append(deepcopy(body["candidates"][0]))
    elif change == "trial_cap":
        body["limits"]["trials"] = 0
    else:
        body["candidates"][0]["contract_hash"] = "wrong"
    with pytest.raises(ValueError):
        SyntheticBatchServiceV1(root, policy).request(body)


def test_stale_source_and_expired_confirmation(prepared_batch):
    root, policy, body = prepared_batch
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    path = root / "data/research/data_routing/routing_policy.json"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="BINDING_CHANGED"):
            service.confirm(identifier, approval(preview))
        assert not (service._directory(identifier) / "confirmation.json").exists()
    finally:
        path.write_bytes(original)
    expired = SyntheticBatchServiceV1(root, policy, clock=lambda: datetime.fromisoformat(body["expires_at"]) + timedelta(seconds=1))
    with pytest.raises(ValueError, match="CONFIRMATION_EXPIRED"):
        expired.confirm(identifier, approval(preview))


@pytest.mark.parametrize("identifier", [".", "..", "../other", "C:/other"])
def test_batch_identifier_cannot_escape_directory(prepared_batch, identifier):
    root, policy, _ = prepared_batch
    with pytest.raises(ValueError, match="IDENTITY_INVALID"):
        SyntheticBatchServiceV1(root, policy).inspect(identifier)


@pytest.mark.parametrize("source", ["creation_receipt", "family"])
def test_formal_creation_immutable_evidence_is_required(prepared_batch, source):
    root, policy, body = prepared_batch
    creation = json.loads((root / "r2-formal-objective-creation-evidence.json").read_bytes())["receipt"]
    if source == "family":
        path = root / creation["multiple_testing_family_ref"]
    else:
        from chanlun_trader.research_factory.objective_execution_binding import ResearchProposalGovernanceServiceV2
        path = ResearchProposalGovernanceServiceV2(root, execution_policy=policy)._receipt_path(creation["proposal_id"])
    before = path.read_bytes()
    try:
        path.write_bytes(before + b"\n")
        with pytest.raises(ValueError, match="TRANSACTION_INVALID|STATISTICAL_FAMILY_CHANGED"):
            SyntheticBatchServiceV1(root, policy).request(body)
    finally:
        path.write_bytes(before)


def test_single_candidate_actual_bounded_services(prepared_batch):
    root, policy, body = prepared_batch
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    result = service.run(identifier)
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / root.name
    (output / "bounded-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for path in (service._directory(identifier) / "executions").glob("*/*"):
        (output / (path.parent.name + "-" + path.name)).write_bytes(path.read_bytes())
    assert result["status"] == "COMPLETED", result["state"]
    assert len(result["state"]["completed_actions"]) == 2
    batch_id = preview["bindings"][0]["batch_id"]
    batch_root = root / f"data/research/research_factory/batches/{batch_id}"
    ledger = json.loads((batch_root / "factory_trial_ledger.json").read_bytes())
    assert ledger["events"][-1]["status"] == "COMPLETED"
    assert ledger["events"][-1]["performance_accessed"] is True
    budget_path = batch_root / "search_budget_registry.json"
    original = budget_path.read_bytes()
    assert all(row["used"] == 1 and row["reserved"] == 0 for row in json.loads(original)["buckets"])
    assert service.run(identifier)["status"] == "COMPLETED"
    assert budget_path.read_bytes() == original
    with pytest.raises(ValueError, match="CANONICAL_BUDGET_EXHAUSTED"):
        service.request(body)
    from chanlun_trader.research_factory.predictive_trial_start import PredictiveTrialStartServiceV1, PredictiveTrialStartError
    with pytest.raises(PredictiveTrialStartError, match="批次委托"):
        PredictiveTrialStartServiceV1(root)._authorization_rows(body["candidates"][0]["objective_id"])
    from chanlun_trader.research_factory.synthetic_batch_delegation import batch_performance_boundary
    from types import SimpleNamespace
    stripped = SimpleNamespace(candidate_id=body["candidates"][0]["candidate_id"], metadata={})
    with pytest.raises(PermissionError, match="CANONICAL_DELEGATION_METADATA"):
        with batch_performance_boundary(root, body["candidates"][0]["objective_id"], stripped):
            pytest.fail("不能删除 metadata 后借旧性能入口消费批次委托")
    from chanlun_trader.research_factory.synthetic_batch_delegation import BatchPredictiveTrialStartServiceV1, BatchPredictiveGovernanceServiceV1
    execution_id = result["state"]["completed_actions"][-1]["execution_id"]
    adapter = BatchPredictiveTrialStartServiceV1(service, identifier, execution_id, body["candidates"][0])
    with pytest.raises(PermissionError, match="RETRY_NOT_AUTHORIZED"):
        adapter.confirm_resume(body["candidates"][0]["objective_id"], {"confirmed": True})
    with pytest.raises(PermissionError, match="SETTLEMENT_ONLY"):
        adapter.recover(body["candidates"][0]["objective_id"])
    with pytest.raises(PermissionError, match="ACTION_NOT_AUTHORIZED"):
        BatchPredictiveGovernanceServiceV1(service, identifier, execution_id).confirm(body["candidates"][0]["objective_id"],
            {"decision_type": "END_CANDIDATE_RESEARCH_DIRECTION", "authorization_id": execution_id})


def test_multiple_explicit_candidates_advance_without_per_tick_confirmation():
    root, policy, body = prepare_batch(multiple=True)
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    result = service.run(identifier)
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / root.name
    (output / "bounded-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for path in (service._directory(identifier) / "executions").glob("*/*"):
        (output / (path.parent.name + "-" + path.name)).write_bytes(path.read_bytes())
    assert result["status"] == "COMPLETED", result["state"]
    assert len(result["state"]["completed_actions"]) == 4
    for binding in preview["bindings"]:
        budget = json.loads((root / binding["budget_ref"]).read_bytes())
        assert all(row["used"] == 1 and row["reserved"] == 0 for row in budget["buckets"])
        decisions = root / f"reports/research_orchestrator_v2/{binding['candidate']['objective_id']}/predictive_governance_decisions.jsonl"
        records = [json.loads(row) for row in decisions.read_text(encoding="utf-8").splitlines()]
        assert all(row["actor"] == row["authorization_origin"] == "BATCH_DELEGATED" for row in records)


def test_worker_exit_after_performance_consumes_original_budget_without_retry(monkeypatch):
    root, policy, body = prepare_batch()
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    assert service.execute_next(identifier)["status"] == "ACTIVE"
    from chanlun_trader.research_factory import synthetic_batch
    bounded = synthetic_batch.run_bounded_worker

    def inject_actual_process_exit(command, **kwargs):
        # 只换入会退出73的测试驱动；OS约束、正式服务与授权核验仍实际执行。
        replacement = [command[0], str(Path(__file__).with_name("batch_interruption_worker.py")), *command[3:]]
        return bounded(replacement, **kwargs)

    monkeypatch.setattr(synthetic_batch, "run_bounded_worker", inject_actual_process_exit)
    result = service.execute_next(identifier)
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / root.name
    (output / "interrupted-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for path in (service._directory(identifier) / "executions").glob("*/*"):
        (output / (path.parent.name + "-" + path.name)).write_bytes(path.read_bytes())
    assert (root / "r3-crash-after-performance.txt").is_file()
    assert result["status"] == "BLOCKED"
    assert result["state"]["failed_actions"][0]["returncode"] == 73
    binding = preview["bindings"][0]
    budget_path = root / binding["budget_ref"]
    before = budget_path.read_bytes()
    assert all(row["used"] == 1 and row["reserved"] == 0 for row in json.loads(before)["buckets"])
    assert SyntheticBatchServiceV1(root, policy).run(identifier)["status"] == "BLOCKED"
    assert budget_path.read_bytes() == before


def test_revoke_between_process_creation_and_admission_blocks_real_worker(prepared_batch, monkeypatch):
    root, policy, original = prepared_batch
    body = deepcopy(original)
    body["actions"] = ["RUN_STRUCTURAL_PREFLIGHT"]
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    from chanlun_trader.research_factory import synthetic_batch
    bounded = synthetic_batch.run_bounded_worker

    def revoke_before_admission(command, **kwargs):
        started = kwargs["on_started"]

        def revoke(pid):
            service.control(identifier, "revoke", approval(preview))
            started(pid)

        kwargs["on_started"] = revoke
        return bounded(command, **kwargs)

    monkeypatch.setattr(synthetic_batch, "run_bounded_worker", revoke_before_admission)
    result = service.execute_next(identifier)
    assert result["status"] == "REVOKED"
    assert result["state"]["active_execution"] is None
    rows = result["state"]["completed_actions"] + result["state"]["failed_actions"]
    assert rows[0]["worker_pid"] is None
    assert rows[0]["action_started_at"] is None
    assert rows[0]["launch_error"]["type"] == "PermissionError"


def test_concurrent_claim_and_stop_do_not_create_more_permits(prepared_batch):
    from concurrent.futures import ThreadPoolExecutor
    root, policy, original = prepared_batch
    body = deepcopy(original)
    body["actions"] = ["RUN_STRUCTURAL_PREFLIGHT"]
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    active = service._claim(identifier)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(PermissionError, match="CONCURRENCY_LIMIT"):
            pool.submit(service._claim, identifier).result()
    assert service.inspect(identifier)["state"]["active_execution"] == active
    service.control(identifier, "stop", approval(preview))
    with pytest.raises(PermissionError, match="PARENT_AUTHORIZATION"):
        service._claim(identifier)


def test_copied_approval_and_external_domain_are_rejected(prepared_batch, tmp_path):
    import shutil
    root, policy, original = prepared_batch
    body = deepcopy(original)
    body["actions"] = ["RUN_STRUCTURAL_PREFLIGHT"]
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    copied = tmp_path / "isolated-copy"
    copied.mkdir()
    shutil.copytree(service.directory, copied / "reports/synthetic_batches_v1")
    with pytest.raises(ValueError, match="CANONICAL_RECORD_INVALID"):
        SyntheticBatchServiceV1(copied, policy).inspect(identifier)
    with pytest.raises(PermissionError, match="SYNTHETIC_GOVERNANCE"):
        SyntheticBatchServiceV1(root, ExecutionPolicy("GOVERNED", "EXTERNAL"))
