"""正式 API 使用真实治理和实际受限 worker；默认只读与旧 CP 入口保持关闭。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.synthetic_batch import SyntheticBatchServiceV1
from chanlun_trader.webapp import create_app
from test_synthetic_batch_contract import prepare_batch, approval

BASE = "/api/research-engineering/batches"


@pytest.fixture(scope="module")
def web_batch():
    return prepare_batch()


def test_real_api_preview_confirmation_run_and_readonly_history(web_batch):
    root, policy, body = web_batch
    with TestClient(create_app(root, policy)) as client:
        context = client.get(BASE + "/context")
        assert context.status_code == 200, context.text
        assert context.json()["candidates"][0]["status"] == "READY"
        requested = client.post(BASE + "/request", json=body)
        assert requested.status_code == 200, requested.text
        preview = requested.json()
        route = BASE + "/" + preview["batch_authorization_id"]
        assert client.get(route).json()["status"] == "REQUESTED"
        assert client.post(route + "/run", json={}).status_code == 409
        assert client.post(route + "/confirm", json={"approved": True}).status_code == 409
        assert client.post(route + "/confirm", json=approval(preview)).json()["status"] == "ACTIVE"
        result = client.post(route + "/run", json={})
        output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / root.name
        (output / "web-run-result.json").write_text(result.text, encoding="utf-8")
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "COMPLETED", result.text
        context = client.get(BASE + "/context").json()
        assert context["candidates"][0]["status"] == "BLOCKED"
        assert context["candidates"][0]["reason"] == "BATCH_CANONICAL_BUDGET_EXHAUSTED"
        objective_id = body["candidates"][0]["objective_id"]
        assert client.post(f"/api/research-console/{objective_id}/predictive/trial/start", json={"confirmed": True}).status_code == 403
    service = SyntheticBatchServiceV1(root, policy)
    directory = service._directory(preview["batch_authorization_id"])
    before = {path.relative_to(root).as_posix(): path.read_bytes() for path in directory.rglob("*.json")}
    budget_path = root / preview["bindings"][0]["budget_ref"]
    budget_before = budget_path.read_bytes()
    with TestClient(create_app(root, ExecutionPolicy("READ_ONLY", "SYNTHETIC"))) as client:
        assert client.get(route).json()["status"] == "COMPLETED"
        assert client.get(BASE + "/context").json()["actions_allowed"] is False
        assert client.post(route + "/run", json={}).status_code == 403
        assert client.post(route + "/recover", json={}).status_code == 403
    assert {path.relative_to(root).as_posix(): path.read_bytes() for path in directory.rglob("*.json")} == before
    assert budget_path.read_bytes() == budget_before


def test_future_and_expired_authorizations_can_be_revoked(web_batch):
    root, policy, original = web_batch
    body = deepcopy(original)
    body["actions"] = ["RUN_STRUCTURAL_PREFLIGHT"]
    now = datetime.now(timezone.utc)
    body["effective_at"] = (now + timedelta(minutes=10)).isoformat()
    body["expires_at"] = (now + timedelta(hours=1)).isoformat()
    service = SyntheticBatchServiceV1(root, policy)
    for expired in (False, True):
        preview = service.request(body)
        identifier = preview["batch_authorization_id"]
        assert service.confirm(identifier, approval(preview))["status"] == "NOT_YET_EFFECTIVE"
        current = SyntheticBatchServiceV1(root, policy, clock=lambda: now + timedelta(hours=2)) if expired else service
        assert current.control(identifier, "revoke", approval(preview))["status"] == "REVOKED"
        assert current.confirm(identifier, approval(preview))["status"] == "REVOKED"


def test_readonly_cannot_claim_even_through_service(web_batch):
    root, _, _ = web_batch
    service = SyntheticBatchServiceV1(root, ExecutionPolicy("READ_ONLY", "SYNTHETIC"))
    with pytest.raises(PermissionError, match="SYNTHETIC_GOVERNANCE"):
        service._claim("untrusted")


def test_nonlocal_client_cannot_request_or_confirm(web_batch):
    root, policy, body = web_batch
    with TestClient(create_app(root, policy), client=("10.10.10.10", 4321)) as client:
        assert client.post(BASE + "/request", json=body).status_code == 403
        assert client.post(BASE + "/untrusted/confirm", json={"confirmed": True, "test_confirmation": True}).status_code == 403
