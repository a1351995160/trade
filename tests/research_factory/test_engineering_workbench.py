"""真实服务/API操作合成预览与回放；默认只读、确认和上下文不绕过。"""
import json

from fastapi.testclient import TestClient
import pytest

from test_portfolio_plan import setup
from test_daily_plan import timestamp
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.engineering_workbench import EngineeringWorkbenchV1
from chanlun_trader.webapp import create_app

GOVERNED = ExecutionPolicy("GOVERNED", "SYNTHETIC")
ENDPOINT = "/api/research-engineering/workbench"


def workbench(tmp_path):
    root = tmp_path / "inputs"
    sources, policy, ledger = setup(root, count=1)
    for source in sources.values():
        source["root"] = root / "0"
    return EngineeringWorkbenchV1(root, sources, ledger, policy, tmp_path / "output")


def test_default_readonly_startup_get_and_post_rejection(tmp_path):
    service = workbench(tmp_path)
    before = {path: path.read_bytes() for path in service.root.rglob("*") if path.is_file()}
    with TestClient(create_app(service.root, engineering_workbench=service)) as client:
        view = client.get(ENDPOINT).json()
        assert view["actions_allowed"] is False
        assert view["qualified_strategy_count"] == 0
        for action in ("publish", "advance"):
            assert client.post(ENDPOINT + "/" + action, json={"confirmed": True, "context_hash": view["context_hash"]}).status_code == 403
    assert not service.output_root.exists()
    assert before == {path: path.read_bytes() for path in service.root.rglob("*") if path.is_file()}


def test_real_api_preview_archive_step_and_new_application_recovery(tmp_path):
    service = workbench(tmp_path)
    candidate = next(iter(service.sources))
    plan_at = str(timestamp(service.sources[candidate]["inputs"]["exec_calendar"][0]))
    app = create_app(service.root, GOVERNED, engineering_workbench=service)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        view = client.get(ENDPOINT).json()
        plan = client.get(ENDPOINT, params={"plan_at": plan_at})
        assert plan.status_code == 200, plan.text
        assert plan.json()["entries"] and plan.json()["execution_ready"] is False
        consent = {"confirmed": True, "context_hash": view["context_hash"], "plan_at": plan_at, "candidate_id": candidate, "event_count": 9}
        archive = client.post(ENDPOINT + "/publish", json=consent)
        assert archive.status_code == 200, archive.text
        assert list((service.output_root / "daily").glob("PLAN_*.json"))
        assert list((service.output_root / "portfolio").glob("PORTFOLIO_PLAN_*.json"))
        advanced = client.post(ENDPOINT + "/advance", json=consent)
        assert advanced.status_code == 200, advanced.text
        assert advanced.json()["completed_events"] == 9
        assert advanced.json()["state"]["trades"]
        assert advanced.json()["real_observation_days"] == 0
        assert client.post(ENDPOINT + "/advance", json=consent).status_code == 409
    # GET历史不恢复；新应用的显式动作才核对已有事件并继续。
    with TestClient(create_app(service.root, GOVERNED, engineering_workbench=service), base_url="http://127.0.0.1") as client:
        state = client.get(ENDPOINT).json()
        assert state["paper"][candidate]["completed_events"] == 9
        consent.update(context_hash=state["context_hash"], event_count=10)
        result = client.post(ENDPOINT + "/advance", json=consent)
        assert result.status_code == 200, result.text
        assert result.json()["completed_events"] == 10


@pytest.mark.parametrize("update", [{"confirmed": False}, {"context_hash": "old"}, {"candidate_id": []}])
def test_governed_service_still_rejects_wrong_confirmation_or_identity(tmp_path, update):
    service = workbench(tmp_path)
    payload = {"confirmed": True, "context_hash": service.inspect()["context_hash"], "candidate_id": next(iter(service.sources)), "event_count": 1, **update}
    with TestClient(create_app(service.root, GOVERNED, engineering_workbench=service), base_url="http://127.0.0.1") as client:
        response = client.post(ENDPOINT + "/advance", json=payload)
        assert response.status_code == 409, response.text
    assert not list(service.output_root.rglob("header.json"))


@pytest.mark.parametrize("damage", ["content", "committed_tail"])
def test_damaged_archive_blocks_read_and_further_execution(tmp_path, damage):
    service = workbench(tmp_path)
    candidate = next(iter(service.sources))
    service.advance(GOVERNED, {"confirmed": True, "context_hash": service.inspect()["context_hash"], "candidate_id": candidate, "event_count": 1})
    path = next(service.output_root.rglob("00000000.json"))
    if damage == "committed_tail":
        path.rename(path.with_suffix(".preserved"))
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        value["state"]["cash"] += 1
        path.write_text(json.dumps(value), encoding="utf-8")
    with TestClient(create_app(service.root, GOVERNED, engineering_workbench=service), base_url="http://127.0.0.1") as client:
        assert client.get(ENDPOINT).status_code == 409
        assert client.post(ENDPOINT + "/advance", json={"confirmed": True}).status_code == 409
    assert len(list(service.output_root.rglob("000000*.json"))) == (0 if damage == "committed_tail" else 1)


def test_missing_workbench_and_wrong_root_never_fall_back(tmp_path):
    service = workbench(tmp_path)
    with TestClient(create_app(service.root)) as client:
        assert client.get(ENDPOINT).status_code == 503
    with pytest.raises(ValueError, match="WORKBENCH_APPLICATION_ROOT_CONFLICT"):
        create_app(service.root / "0", engineering_workbench=service)
