"""P3-A 合成工作区与 Web 生命周期认证。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fastapi.testclient import TestClient
from chanlun_trader import webapp
from chanlun_trader.execution_policy import ExecutionPolicy


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


WRITE_ROUTES = sorted({route.path for route in webapp.app.routes if "POST" in getattr(route, "methods", set())})


@pytest.mark.parametrize("mode", [None, "", "CREATE_AND_ACTIVATE", "create_and_activate"])
def test_governance_activation_is_denied_before_confirmation(tmp_path, mode):
    application = webapp.create_app(tmp_path, ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    calls = []

    class ForbiddenServices:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError(name)

    application.state.services = ForbiddenServices()
    with TestClient(application) as client:
        response = client.post("/api/research-console/SYNTHETIC/governance/confirm", json={"confirmed": True, "execution_mode": mode})
    assert response.status_code == 403
    assert response.json()["code"] == "PROCESS_START_DISABLED"
    assert calls == []


def test_valid_pending_trial_is_not_resumed_by_startup(tmp_path, monkeypatch):
    from p3a_pending_trial_fixture import pending_trial_fixture, OBJECTIVE_ID, _confirm_body
    from chanlun_trader.research_factory.predictive_trial_start import PredictiveTrialStartServiceV1
    pending_trial_fixture(tmp_path)
    service = PredictiveTrialStartServiceV1(tmp_path, auto_run=False)
    preview = service.preview(OBJECTIVE_ID)
    assert preview["available"] is True, preview
    receipt = service.confirm(OBJECTIVE_ID, _confirm_body(preview))
    assert receipt["stage"] == "TRIAL_RUNNING"
    before = snapshot(tmp_path)
    calls = []
    monkeypatch.setattr(PredictiveTrialStartServiceV1, "recover_all", lambda self: calls.append("recover_all"))
    monkeypatch.setattr(PredictiveTrialStartServiceV1, "_launch", lambda *args: calls.append("launch"))
    application = webapp.create_app(tmp_path, ExecutionPolicy(workspace_kind="SYNTHETIC"))
    with TestClient(application):
        pass
    assert calls == []
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("route", WRITE_ROUTES)
def test_read_only_denies_before_domain_call(tmp_path, route):
    import re
    application = webapp.create_app(tmp_path, ExecutionPolicy(workspace_kind="SYNTHETIC"))
    calls = []

    class ForbiddenServices:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError(name)

    application.state.services = ForbiddenServices()
    before = snapshot(tmp_path)
    with TestClient(application) as client:
        response = client.post(re.sub(r"\{[^}]+\}", "SYNTHETIC", route), json={})
    assert response.status_code == 403
    assert calls == []
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("service,method", [
    ("predictive_trial_start_service", "recover_all"),
    ("predictive_trial_reauthorization_service", "recover_new_all"),
    ("research_proposal_governance_service", "recover_all"),
    ("research_evolution_ai_design_service", "recover_all"),
    ("candidate_generation_service", "recover_all"),
])
def test_all_startup_recoveries_remain_disabled(tmp_path, monkeypatch, service, method):
    application = webapp.create_app(tmp_path, ExecutionPolicy(workspace_kind="SYNTHETIC"))
    calls = []
    monkeypatch.setattr(getattr(application.state.services, service), method, lambda: calls.append(method))
    with TestClient(application):
        pass
    assert calls == []
    assert snapshot(tmp_path) == {}


def test_real_governance_confirmation_identity_and_two_apps(tmp_path):
    from test_ai_design_approval_v1 import _prepared, OBJECTIVE_ID
    roots = [tmp_path / "a", tmp_path / "b"]
    prepared = [_prepared(root) for root in roots]
    apps = [webapp.create_app(root, ExecutionPolicy(mode, "SYNTHETIC")) for root, mode in zip(roots, ["GOVERNED", "READ_ONLY"])]
    assert apps[0].state.services is not apps[1].state.services
    assert apps[0].state.tasks is not apps[1].state.tasks
    assert apps[0].state.services.research_console_service.cache is not apps[1].state.services.research_console_service.cache
    for root, application in zip(roots, apps):
        for service in vars(application.state.services).values():
            assert service.root == root.resolve()
    before_b = snapshot(roots[1])
    route = f"/api/research-console/{OBJECTIVE_ID}/evolution/ai-design/approval"
    design = prepared[0][1]
    payload = {"confirmed": True, "decision": "APPROVED", "reviewer": "synthetic-human", "idempotency_key": "P3A_APPROVAL", "ai_design_hash": design["design_hash"]}
    with TestClient(apps[0]) as client:
        missing = client.post(route, json={**payload, "confirmed": False})
        wrong = client.post(route, json={**payload, "ai_design_hash": "0" * 64})
        assert missing.status_code == 400
        assert wrong.status_code == 409
        approved = client.post(route, json=payload)
        assert approved.status_code == 200, approved.text
        assert approved.json()["receipt"]["reviewer"] == "synthetic-human"
        predictive = client.post(f"/api/research-console/{OBJECTIVE_ID}/predictive/trial/start", json={"confirmed": True})
        assert predictive.status_code == 403
        assert predictive.json()["code"] == "PHASE2_PREDICTIVE_EXECUTION_DISABLED"
    with TestClient(apps[1]) as client:
        assert client.post(route, json=payload).status_code == 403
    assert snapshot(roots[1]) == before_b
    assert snapshot(roots[0]) != snapshot(roots[1])


@pytest.mark.parametrize("mode", [None, "", "UNKNOWN", "governed"])
def test_unknown_mode_and_environment_never_elevate(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("EXECUTION_MODE", "GOVERNED")
    with TestClient(webapp.create_app(tmp_path)) as client:
        assert client.post("/api/research-console/ai-invocation-mode", json={"mode": "AUTO"}).status_code == 403
    application = webapp.create_app(tmp_path, ExecutionPolicy(mode, "SYNTHETIC"))
    monkeypatch.delenv("EXECUTION_MODE")
    with TestClient(application) as client:
        assert client.post("/api/research-console/ai-invocation-mode", json={"mode": "AUTO"}).status_code == 403


@pytest.mark.parametrize("kind", ["missing", "relative", "traversal", "file"])
def test_invalid_root_fails_closed(tmp_path, kind):
    file = tmp_path / "file"
    file.write_text("fixture", encoding="utf-8")
    roots = {"missing": tmp_path / "absent", "relative": Path("relative"), "traversal": tmp_path / ".." / tmp_path.name, "file": file}
    with pytest.raises(ValueError):
        webapp.create_app(roots[kind], ExecutionPolicy(workspace_kind="SYNTHETIC"))


def test_safe_reads_and_pending_design_recovery_are_read_only(tmp_path, monkeypatch):
    from test_ai_design_approval_v1 import _prepared, OBJECTIVE_ID
    from chanlun_trader.research_factory.research_evolution_ai_design import AI_RESEARCH_DESIGN_STATE_FILENAME
    root, design = _prepared(tmp_path)
    state = root / "reports/research_evolution/ai_design" / OBJECTIVE_ID / AI_RESEARCH_DESIGN_STATE_FILENAME
    state.unlink()
    before = snapshot(root)
    application = webapp.create_app(root, ExecutionPolicy(workspace_kind="SYNTHETIC"))
    with TestClient(application) as client:
        for path in ["/api/research-console/objectives", f"/api/research-console/{OBJECTIVE_ID}/evolution/ai-design", f"/api/research-console/{OBJECTIVE_ID}/autonomous-control-plane", f"/api/research-console/{OBJECTIVE_ID}/safe-runtime-context"]:
            response = client.get(path)
            assert response.status_code == 200, response.text
    assert snapshot(root) == before
    assert not state.exists()


@pytest.mark.parametrize("position", ["root", "nested", "after_creation"])
def test_link_escape_is_rejected_without_touching_reference(tmp_path, position):
    reference = tmp_path / "protected"
    reference.mkdir()
    (reference / "fixture.txt").write_text("reference", encoding="utf-8")
    root = tmp_path / "synthetic"
    root.mkdir()
    application = webapp.create_app(root, ExecutionPolicy(workspace_kind="SYNTHETIC"))
    link = tmp_path / "linked" if position == "root" else root / "reports"
    before = snapshot(reference)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(reference), str(link))
    else:
        link.symlink_to(reference, target_is_directory=True)
    if position == "after_creation":
        with TestClient(application) as client:
            response = client.get("/api/research-console/objectives")
            assert response.status_code == 403
            assert response.json()["code"] == "UNSAFE_RESEARCH_ROOT"
    else:
        with pytest.raises(ValueError, match="LINKED_RESEARCH_ROOT"):
            webapp.create_app(link if position == "root" else root, ExecutionPolicy(workspace_kind="SYNTHETIC"))
    assert snapshot(reference) == before


def test_all_read_routes_do_not_write(tmp_path, monkeypatch):
    import builtins
    import io
    import re
    from test_ai_design_approval_v1 import _prepared, OBJECTIVE_ID
    root, _ = _prepared(tmp_path)
    application = webapp.create_app(root, ExecutionPolicy(workspace_kind="SYNTHETIC"))
    before = snapshot(root)
    writes = []
    original_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open

    def guard(original):
        def checked(file, mode="r", *args, **kwargs):
            if any(c in mode for c in "wax+"):
                writes.append(str(file))
                raise AssertionError("READ_ROUTE_WROTE_BUSINESS_STATE")
            return original(file, mode, *args, **kwargs)
        return checked

    def mutation(*args, **kwargs):
        writes.append(str(args))
        raise AssertionError("READ_ROUTE_MUTATED_FILESYSTEM")

    def checked_os_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            return mutation(path, flags)
        return original_os_open(path, flags, *args, **kwargs)

    with TestClient(application) as client:
        with monkeypatch.context() as patch:
            patch.setattr(builtins, "open", guard(original_open))
            patch.setattr(io, "open", guard(original_io_open))
            patch.setattr(os, "open", checked_os_open)
            for name in ["mkdir", "remove", "rename", "replace", "unlink", "rmdir", "link", "symlink"]:
                patch.setattr(os, name, mutation)
            for route in application.routes:
                if "GET" not in getattr(route, "methods", set()) or not route.path.startswith(("/api/research", "/research/evolution", "/research/candidates")):
                    continue
                path = route.path.replace("{objective_id}", OBJECTIVE_ID)
                path = re.sub(r"\{[^}]+\}", "SYNTHETIC_UNKNOWN", path)
                response = client.get(path)
                assert response.status_code in {200, 400, 404, 409, 503}, (path, response.text)
    assert writes == []
    assert snapshot(root) == before


def test_fresh_import_and_startup_have_no_business_effects(tmp_path):
    source = Path(__file__).resolve().parents[2] / "src"
    root = tmp_path / "synthetic"
    root.mkdir()
    reference = tmp_path / "protected"
    reference.mkdir()
    sentinel = reference / "fixture.txt"
    sentinel.write_text("synthetic reference", encoding="utf-8")
    before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    reference_before = snapshot(reference)
    script = r'''
import os, sys, pathlib, threading, socket, asyncio
loop = asyncio.new_event_loop()
root, reference = map(pathlib.Path, sys.argv[1:])
counts = dict(writes=0, executors=0)
def audit(event, args):
    if event in {"subprocess.Popen", "os.system", "socket.connect"}:
        counts["executors"] += 1
        raise AssertionError(event)
    if event == "open":
        path, mode, flags = args
        write = isinstance(mode, str) and any(c in mode for c in "wax+") or isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        if write:
            counts["writes"] += 1
            raise AssertionError((event, path))
    if event in {"os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.link", "os.symlink"}:
        counts["writes"] += 1
        raise AssertionError(event)
sys.addaudithook(audit)
def forbidden(*args, **kwargs):
    counts["executors"] += 1
    raise AssertionError("research thread")
threading.Thread.start = forbidden
from chanlun_trader import webapp
from chanlun_trader.execution_policy import ExecutionPolicy
import runpy
runpy.run_path(str(pathlib.Path(webapp.__file__).resolve().parents[2] / "scripts" / "run_ui.py"))
import asyncio
async def check():
    for application in (webapp.app, webapp.create_app(root, ExecutionPolicy(workspace_kind="SYNTHETIC"))):
        async with application.router.lifespan_context(application):
            assert application.state.recovery_status == "RECOVERY_DISABLED"
loop.run_until_complete(check())
loop.close()
assert counts == dict(writes=0, executors=0), counts
print(counts)
'''
    env = {**os.environ, "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-B", "-c", script, str(root), str(reference)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "'writes': 0" in result.stdout
    assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == before
    assert snapshot(reference) == reference_before
    assert list(root.iterdir()) == []
