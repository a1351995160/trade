from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import threading

import pytest

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.autonomous_control_plane import AutonomousControlPlaneError, AutonomousResearchControlPlaneV1, ResearchActionV1
from chanlun_trader.research_factory.candidate_executable_materialization import CandidateExecutableMaterializationManagerV1
from chanlun_trader.research_factory.candidate_generation import CandidateGenerationManagerV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock, MutationBusyError
from test_candidate_generation_governance_v1 import OBJECTIVE_ID, _prepare
from test_candidate_executable_materialization_v1 import _bridge_fixture


POLICY = ExecutionPolicy(mode="GOVERNED", workspace_kind="SYNTHETIC")
WORKER = Path(__file__).with_name("p3b_process_worker.py")


def plane(root):
    return AutonomousResearchControlPlaneV1(root, execution_policy=POLICY)


def snapshot(root):
    # Windows 对被锁的字节强制拒读；空锁文件以 size=0 和 mtime 验证。
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes() if p.stat().st_size else b"").hexdigest(), p.stat().st_mtime_ns) if p.is_file() else "DIRECTORY" for p in root.rglob("*")}


def launch(root, operation, objective=OBJECTIVE_ID):
    return subprocess.Popen([sys.executable, str(WORKER), str(root), objective, operation], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")


def finish(process, expected=0, command=None):
    stdout, stderr = process.communicate(command, timeout=30)
    assert process.returncode == expected, (stdout, stderr)
    if expected == 0:
        assert '"protected_accesses": 0' in stderr
    return json.loads(stdout.splitlines()[-1]) if stdout.strip() else None


def setup_stage(tmp_path, stage):
    if stage == "proposal":
        return _prepare(tmp_path)
    root, proposal, _ = _bridge_fixture(tmp_path)
    if stage == "materialization":
        manager = CandidateExecutableMaterializationManagerV1(root)
        preview = manager.create_preview(OBJECTIVE_ID, proposal["proposal_id"])
        with pytest.raises(RuntimeError, match="SYNTHETIC_CRASH_INJECTED"):
            CandidateExecutableMaterializationManagerV1(root, crash_at="after_confirmation_receipt").confirm(
                OBJECTIVE_ID, proposal["proposal_id"], {"confirmed": True, "reviewer": "p3b-reviewer", "preview_hash": preview["preview_hash"], "idempotency_key": "P3B_CONFIRM"})
    return root


@pytest.mark.parametrize("stage", ["proposal", "preview", "materialization"])
def test_process_exit_after_effect_public_tick_recovers_without_provider(tmp_path, stage):
    root = setup_stage(tmp_path, stage)
    finish(launch(root, "exit_after"), expected=92)
    before = snapshot(root)
    recovered = finish(launch(root, "tick"))
    assert recovered["execution"]["recovered"] is True
    assert recovered["continue_loop"] is False
    evidence = [json.loads(line) for line in (root / "process_evidence.jsonl").read_text().splitlines()]
    assert [row["event"] for row in evidence] == ["provider_attempt", "domain_returned"]
    assert {row["operation"] for row in evidence} == {"exit_after"}
    after = snapshot(root)
    changed = [name for name in before if before[name] != after[name]]
    assert changed == [f"reports/research_control_plane/{OBJECTIVE_ID}/action_execution_receipts.jsonl".replace("/", os.sep)]
    rows = plane(root)._journal(OBJECTIVE_ID).read()
    assert [row.execution_status for row in rows] == ["STARTED", "COMPLETED"]
    assert rows[-1].recovered and rows[-1].attempt == 1


@pytest.mark.parametrize("stale", [False, True])
def test_process_exit_before_effect_same_key_retry_or_stale_deny(tmp_path, stale):
    root = _prepare(tmp_path)
    finish(launch(root, "exit_before"), expected=91)
    journal = plane(root)._journal(OBJECTIVE_ID)
    key = journal.read()[0].idempotency_key
    if stale:
        path = root / "data/research/data_capability.json"
        payload = json.loads(path.read_text())
        payload["datasets"][0]["data_version"] = "P3B_STALE"
        path.write_text(json.dumps(payload), encoding="utf-8")
    result = finish(launch(root, "recover"))
    if stale:
        assert result["reason_code"] == "STALE_RESEARCH_ACTION"
        assert not (root / "process_evidence.jsonl").exists()
        assert len(journal.read()) == 1
    else:
        assert result["execution_status"] == "COMPLETED"
        assert result["receipt"]["attempt"] == 2
        assert {row.idempotency_key for row in journal.read()} == {key}


@pytest.mark.parametrize("operation", ["hold_tick", "hold_domain"])
def test_cp_and_real_domain_entry_share_process_boundary(tmp_path, operation):
    root = _prepare(tmp_path)
    action = plane(root).inspect(OBJECTIVE_ID)["decision"]["selected_action"]
    owner = launch(root, operation)
    try:
        assert owner.stdout.readline().strip() == "LOCKED"
        result = finish(launch(root, "tick"))
        assert result["stop_reason"] == "CONTROL_PLANE_CONCURRENT_RUN"
        before = snapshot(root)
        for read in (lambda: plane(root).inspect(OBJECTIVE_ID), lambda: plane(root).recover(OBJECTIVE_ID, dry_run=True), lambda: plane(root).tick(OBJECTIVE_ID, dry_run=True), lambda: plane(root).execute_action(action, dry_run=True)):
            assert read()["reason_code"] == "CONTROL_PLANE_CONCURRENT_RUN"
        assert snapshot(root) == before
        finish(owner, command="GO\n")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.communicate()
    assert len(list(root.glob("reports/research_candidates/proposals/*/CANDIDATE_PROPOSAL.json"))) == 1


def test_owner_killed_without_finally_allows_fresh_process_retry(tmp_path):
    root = _prepare(tmp_path)
    owner = launch(root, "hold_tick")
    assert owner.stdout.readline().strip() == "LOCKED"
    owner.kill()
    owner.communicate(timeout=10)
    result = finish(launch(root, "recover"))
    assert result["execution_status"] == "COMPLETED"
    assert result["receipt"]["attempt"] == 2


@pytest.mark.parametrize("damage", ["none", "matched", "stale", "wrong_identity", "half_line", "hash", "missing", "intent", "legacy", "multi"])
def test_all_public_dry_runs_are_read_only(tmp_path, damage, monkeypatch):
    root = _prepare(tmp_path)
    service = plane(root)
    action = ResearchActionV1.from_dict(service.inspect(OBJECTIVE_ID)["decision"]["selected_action"])
    journal = service._journal(OBJECTIVE_ID)
    journal.begin(action)
    if damage == "matched":
        CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
    elif damage == "wrong_identity":
        CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
        path = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "CANDIDATE_PROPOSAL.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["objective_id"] = "WRONG_OBJECTIVE"
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif damage == "stale":
        path = root / "data/research/data_capability.json"
        data = json.loads(path.read_text())
        data["datasets"][0]["data_version"] = "STALE"
        path.write_text(json.dumps(data), encoding="utf-8")
    elif damage in {"half_line", "hash"}:
        with journal.path.open("a", encoding="utf-8") as handle:
            handle.write('{"broken":' if damage == "half_line" else '{}\n')
    elif damage == "missing":
        journal.path.unlink()
    elif damage == "intent":
        journal._intent_path(action.to_dict()).write_text("{}", encoding="utf-8")
    elif damage == "legacy":
        row = json.loads(journal.path.read_text())
        row.pop("execution_intent_hash")
        row["schema_version"] = "autonomous-action-execution-receipt-v1"
        row["receipt_hash"] = stable_hash({k: v for k, v in row.items() if k != "receipt_hash"})
        journal.path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        journal._intent_path(action.to_dict()).unlink()
    elif damage == "multi":
        journal.begin(replace(action, action_id="SECOND_ACTION"))
    before = snapshot(root)
    calls = [lambda: service.inspect(OBJECTIVE_ID), lambda: service.tick(OBJECTIVE_ID, dry_run=True), lambda: service.execute_action(action, dry_run=True), lambda: service.recover(OBJECTIVE_ID, dry_run=True)]
    attempted = []
    def forbidden(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("DRY_RUN_WRITE_OR_PROVIDER")
    original_open = Path.open
    def read_open(path, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            return forbidden()
        return original_open(path, mode, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(AutonomousResearchControlPlaneV1, "_execute_domain_action", forbidden)
        patch.setattr(type(journal), "_append", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        patch.setattr(Path, "open", read_open)
        for call in calls:
            result = call()
            assert result
            assert snapshot(root) == before
        assert attempted == []
    if damage not in {"none", "matched"}:
        result = service.recover(OBJECTIVE_ID)
        assert result["execution_status"] == "DENY"


@pytest.mark.parametrize("field,value", [("human_confirmation_required", False), ("required_capabilities", []), ("required_authorities", []), ("candidate_id", "WRONG"), ("expected_domain_identity", {}), ("budget_effect", {"mode": "FREE"})])
def test_rehashed_action_cannot_expand_permission(tmp_path, field, value):
    root = _prepare(tmp_path)
    service = plane(root)
    action = service.inspect(OBJECTIVE_ID)["decision"]["selected_action"]
    if field == "human_confirmation_required":
        CandidateGenerationManagerV1(root).generate_proposal(OBJECTIVE_ID)
        action = service.inspect(OBJECTIVE_ID)["decision"]["selected_action"]
    action[field] = value
    forged = ResearchActionV1.create(**{k: v for k, v in action.items() if k not in {"schema_version", "action_id", "idempotency_key"}})
    before = snapshot(root)
    result = service.execute_action(forged, dry_run=True)
    assert result["execution_status"] == "DENY"
    assert snapshot(root) == before


def test_default_external_and_unknown_policies_refuse_recovery(tmp_path):
    root = _prepare(tmp_path)
    for policy in (ExecutionPolicy(), ExecutionPolicy(mode="GOVERNED"), ExecutionPolicy(mode="UNKNOWN", workspace_kind="SYNTHETIC")):
        before = snapshot(root)
        with pytest.raises(AutonomousControlPlaneError, match="GOVERNED"):
            AutonomousResearchControlPlaneV1(root, execution_policy=policy).recover(OBJECTIVE_ID)
        assert snapshot(root) == before


def test_lock_owner_threads_nested_and_independent_objectives(tmp_path):
    first = ObjectiveMutationLock(tmp_path, "objective:A")
    first.acquire()
    errors = []
    def contender():
        for operation in (first.release, lambda: ObjectiveMutationLock(tmp_path, "objective:A").acquire()):
            try:
                operation()
            except MutationBusyError:
                errors.append(True)
    thread = threading.Thread(target=contender)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive() and len(errors) == 2
    with ObjectiveMutationLock(tmp_path, "objective:A"), ObjectiveMutationLock(tmp_path, "objective:B"):
        pass
    first.release()
    replacement = ObjectiveMutationLock(tmp_path, "objective:A")
    replacement.acquire()
    first.release()
    assert replacement.owner is not None
    replacement.release()


def test_completed_history_and_legacy_completed_remain_read_only(tmp_path):
    root = _prepare(tmp_path)
    service = plane(root)
    completed = service.tick(OBJECTIVE_ID)
    action = completed["decision"]["selected_action"]
    receipt = completed["execution"]["receipt"]
    path = root / "data/research/data_capability.json"
    payload = json.loads(path.read_text())
    payload["datasets"][0]["data_version"] = "AFTER_COMPLETION"
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = snapshot(root)
    replay = service.execute_action(action, dry_run=True)
    assert replay["receipt"] == receipt and replay["safe_to_advance"] is False
    assert snapshot(root) == before
    journal = service._journal(OBJECTIVE_ID)
    legacy = dict(receipt)
    legacy.pop("execution_intent_hash")
    legacy["schema_version"] = "autonomous-action-execution-receipt-v1"
    legacy["receipt_hash"] = stable_hash({key: value for key, value in legacy.items() if key != "receipt_hash"})
    journal.path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    journal._intent_path(action).unlink()
    before = snapshot(root)
    assert service.execute_action(action, dry_run=True)["receipt"] == legacy
    assert snapshot(root) == before


def test_revoked_confirmation_blocks_retry_and_outcome_injection_is_rejected(tmp_path):
    root = setup_stage(tmp_path, "materialization")
    finish(launch(root, "exit_before"), expected=91)
    journal = plane(root)._journal(OBJECTIVE_ID)
    action = journal.pending()[0]
    confirmation = root / "reports/research_candidates/proposals" / OBJECTIVE_ID / "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json"
    confirmation.unlink()
    before = snapshot(root)
    assert plane(root).recover(OBJECTIVE_ID)["execution_status"] == "DENY"
    assert snapshot(root) == before
    intent_path = journal._intent_path(action)
    envelope = json.loads(intent_path.read_text())
    envelope["action"]["win_rate"] = 0.99
    envelope["intent_hash"] = stable_hash(envelope["action"])
    intent_path.write_text(json.dumps(envelope), encoding="utf-8")
    before = snapshot(root)
    assert plane(root).recover(OBJECTIVE_ID, dry_run=True)["execution_status"] == "DENY"
    assert snapshot(root) == before


def test_competing_recoverers_after_owner_death_commit_once(tmp_path):
    root = _prepare(tmp_path)
    finish(launch(root, "exit_before"), expected=91)
    workers = [launch(root, "recover"), launch(root, "recover")]
    results = [finish(worker) for worker in workers]
    assert sum(result["execution_status"] == "COMPLETED" for result in results) == 1
    assert len(list(root.glob("reports/research_candidates/proposals/*/CANDIDATE_PROPOSAL.json"))) == 1
    evidence = [json.loads(line) for line in (root / "process_evidence.jsonl").read_text().splitlines()]
    assert [row["event"] for row in evidence].count("provider_attempt") == 1


def test_cli_and_web_tick_only_recover_explicitly(tmp_path):
    from fastapi.testclient import TestClient
    from chanlun_trader.webapp import create_app
    root = _prepare(tmp_path)
    finish(launch(root, "exit_after"), expected=92)
    before = snapshot(root)
    command = [sys.executable, "-m", "chanlun_trader.research_factory.autonomous_control_plane", "--root", str(root), "--objective-id", OBJECTIVE_ID, "--recover", "--dry-run", "--json"]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["reason_code"] == "WOULD_RECOVER_COMPLETED"
    assert snapshot(root) == before
    with TestClient(create_app(root, POLICY), client=("127.0.0.1", 50000)) as client:
        response = client.get(f"/api/research-console/{OBJECTIVE_ID}/autonomous-control-plane")
        assert response.status_code == 200
        assert snapshot(root) == before
        response = client.post(f"/api/research-console/{OBJECTIVE_ID}/autonomous-control-plane/tick", json={"dry_run": False})
        assert response.status_code == 200, response.text
        assert response.json()["execution"]["recovered"] is True


def test_managed_daemon_and_orchestrator_ingest_are_blocked(tmp_path):
    from chanlun_trader.research_daemon import ResearchDaemon
    from chanlun_trader.research_factory.autonomous_orchestrator_v2 import CanonicalOrchestratorRuntimeV2
    root = _prepare(tmp_path)
    finish(launch(root, "exit_before"), expected=91)
    before = snapshot(root)
    with pytest.raises(MutationBusyError, match="REQUIRES_EXPLICIT"):
        ResearchDaemon(root, objective_id=OBJECTIVE_ID).run_once()
    with pytest.raises(MutationBusyError, match="REQUIRES_EXPLICIT"):
        CanonicalOrchestratorRuntimeV2(root, OBJECTIVE_ID).ingest_ai_batch(root / "absent.json", {})
    assert snapshot(root) == before


def test_independent_objective_and_normalized_root_process_locks(tmp_path):
    root = _prepare(tmp_path)
    owner = launch(root, "hold_lock")
    try:
        assert owner.stdout.readline().strip() == "LOCKED"
        with ObjectiveMutationLock(root / ".", "objective:ANOTHER_OBJECTIVE"):
            pass
        with pytest.raises(MutationBusyError):
            ObjectiveMutationLock(root / ".", "objective:" + OBJECTIVE_ID).acquire()
        finish(owner, command="GO\n")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.communicate()


def test_shared_graph_reloads_inside_resource_transaction(tmp_path):
    first, second = launch(tmp_path, "graph_A", "A"), launch(tmp_path, "graph_B", "B")
    try:
        assert first.stdout.readline().strip() == "LOADED"
        assert second.stdout.readline().strip() == "LOADED"
        finish(first, command="GO\n")
        result = finish(second, command="GO\n")
        assert {node["node_id"] for node in result["nodes"]} == {"graph_A", "graph_B"}
    finally:
        for worker in (first, second):
            if worker.poll() is None:
                worker.kill()
                worker.communicate()
