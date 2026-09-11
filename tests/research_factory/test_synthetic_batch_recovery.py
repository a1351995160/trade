"""实际控制者退出与新的控制进程结算，禁止重造批准或恢复额度。"""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from chanlun_trader.research_factory.synthetic_batch import SyntheticBatchServiceV1
from test_synthetic_batch_contract import prepare_batch, approval


def test_controller_exit_new_process_recovery_is_not_new_execution():
    root, policy, body = prepare_batch()
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    budget_path = root / preview["bindings"][0]["budget_ref"]
    before = budget_path.read_bytes()
    source = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source / "tests/isolation"), str(source / "src")]))
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / root.name
    for index, action in enumerate(("claim_exit", "recover", "recover")):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("batch_controller_worker.py")),
            str(root), identifier, action], cwd=source, env=environment, capture_output=True, timeout=30)
        (output / f"controller-{index}-stdout.bin").write_bytes(result.stdout)
        (output / f"controller-{index}-stderr.bin").write_bytes(result.stderr)
        assert result.returncode == (73 if action == "claim_exit" else 0), result.stderr.decode()
    result = json.loads((root / "r3-controller-recovery-result.json").read_bytes())
    assert result["status"] == "BLOCKED"
    assert result["state"]["active_execution"] is None
    assert result["state"]["completed_actions"] == []
    assert len(result["state"]["failed_actions"]) == 1
    assert service.run(identifier)["status"] == "BLOCKED"
    assert budget_path.read_bytes() == before
    assert not (root / f"data/research/research_factory/batches/{preview['bindings'][0]['batch_id']}/factory_trial_ledger.json").exists()


def test_expiry_after_actual_confirmation_prevents_claim():
    root, policy, body = prepare_batch()
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    expired = SyntheticBatchServiceV1(root, policy,
        clock=lambda: datetime.fromisoformat(body["expires_at"]) + timedelta(seconds=1))
    with pytest.raises(PermissionError, match="PARENT_AUTHORIZATION"):
        expired._claim(identifier)
    assert expired.inspect(identifier)["status"] == "EXPIRED"
    assert expired.inspect(identifier)["state"]["active_execution"] is None


def test_insufficient_actual_worker_memory_blocks_domain_configuration():
    root, policy, body = prepare_batch()
    body["limits"]["memory_mib"] = 8
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    budget_path = root / preview["bindings"][0]["budget_ref"]
    before = budget_path.read_bytes()
    result = service.execute_next(identifier)
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / root.name
    (output / "resource-blocked.json").write_text(json.dumps(result), encoding="utf-8")
    for path in (service._directory(identifier) / "executions").glob("*/*"):
        (output / (path.parent.name + "-" + path.name)).write_bytes(path.read_bytes())
    assert result["status"] == "BLOCKED"
    assert result["state"]["failed_actions"][0]["action_started_at"] is None
    assert budget_path.read_bytes() == before


def test_recovery_rolls_forward_durable_revocation_never_old_active_head():
    root, policy, body = prepare_batch()
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    source = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source / "tests/isolation"), str(source / "src")]))
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("batch_controller_worker.py")),
        str(root), identifier, "revoke_head_exit"], cwd=source, env=environment, capture_output=True, timeout=30)
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / root.name
    (output / "revoke-head-stdout.bin").write_bytes(result.stdout)
    (output / "revoke-head-stderr.bin").write_bytes(result.stderr)
    assert result.returncode == 73, result.stderr.decode()
    with pytest.raises(ValueError, match="HISTORY_HEAD_CONFLICT"):
        service.inspect(identifier)
    recovered = SyntheticBatchServiceV1(root, policy).recover(identifier)
    assert recovered["status"] == "REVOKED"
    assert recovered["state"]["revision"] == 1
    assert service.confirm(identifier, approval(preview))["status"] == "REVOKED"
    with pytest.raises(PermissionError, match="PARENT_AUTHORIZATION"):
        service._claim(identifier)
