"""同一领域操作经 Web、CLI、CP 的一致边界及损坏/并发场景。"""
import contextlib
import io
import json

from fastapi.testclient import TestClient
import pytest

from chanlun_trader import webapp
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.autonomous_control_plane import main
from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
from p3c_scenario import Scenario
from test_restart_recovery_v1 import launch, finish, snapshot


def tick(scenario, entry, dry_run=False):
    if entry == "CP":
        return scenario.plane.tick(scenario.objective_id, dry_run=dry_run)
    if entry == "CLI":
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["--root", str(scenario.root), "--objective-id", scenario.objective_id, "--tick", "--json", "--governed-synthetic", *(["--dry-run"] if dry_run else [])])
        assert status == 0, output.getvalue()
        return json.loads(output.getvalue())
    app = webapp.create_app(scenario.root, ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    with TestClient(app) as client:
        assert app.state.recovery_status == "RECOVERY_DISABLED"
        response = client.post(f"/api/research-console/{scenario.objective_id}/autonomous-control-plane/tick", json={"dry_run": dry_run})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("entry", ["CP", "CLI", "Web"])
def test_equivalent_entries_complete_same_legal_chain_and_preserve_dry_run(tmp_path, entry):
    scenario = Scenario(tmp_path).initialize()
    scenario.design()
    before = snapshot(tmp_path)
    pending = tick(scenario, entry, True)
    assert pending["decision"]["selected_action"]["action_type"] == "WAIT_FOR_AI_DESIGN_CONFIRMATION"
    assert snapshot(tmp_path) == before
    scenario.approve()
    for manual in (scenario.freeze, scenario.confirm):
        before = snapshot(tmp_path)
        tick(scenario, entry, True)
        assert snapshot(tmp_path) == before
        assert tick(scenario, entry)["execution"]["execution_status"] == "COMPLETED"
        manual()
    budget = scenario.budget_path.read_bytes()
    assert tick(scenario, entry)["decision"]["selected_action"]["action_type"] == "RUN_STRUCTURAL_PREFLIGHT"
    scenario.structural()
    assert tick(scenario, entry)["decision"]["selected_action"]["action_type"] == "WAIT_FOR_PREDICTIVE_AUTHORIZATION"
    scenario.authorize()
    final = tick(scenario, entry)
    assert final["authorization"]["authorized"] is True
    assert final["decision"]["permission"]["reason_code"] == "PHASE2_PREDICTIVE_EXECUTION_DISABLED"
    assert scenario.budget_path.read_bytes() == budget
    assert not list(tmp_path.rglob("factory_trial_ledger.json"))


@pytest.mark.parametrize("damage", ["projections", "receipt_half_line", "receipt_hash", "receipt_missing", "intent_missing", "intent_hash"])
def test_projection_loss_is_distinct_from_execution_evidence_loss(tmp_path, damage):
    scenario = Scenario(tmp_path).initialize()
    scenario.design()
    scenario.approve()
    finish(launch(tmp_path, "exit_after"), expected=92)
    journal = scenario.plane._journal(scenario.objective_id)
    action = journal.pending()[0]
    if damage == "projections":
        # 只删除派生投影；不可把 STARTED/intent 当成投影删掉。
        paths = list(tmp_path.rglob("*checkpoint.json")) + list(tmp_path.rglob("CANDIDATE_PROPOSAL_STATE.json"))
        assert paths
        for path in paths:
            path.unlink()
    elif damage == "receipt_half_line":
        with journal.path.open("a", encoding="utf-8") as stream:
            stream.write('{"partial":')
    elif damage == "receipt_hash":
        row = json.loads(journal.path.read_bytes())
        row["receipt_hash"] = "WRONG"
        journal.path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    elif damage == "receipt_missing":
        journal.path.unlink()
    elif damage == "intent_missing":
        journal._intent_path(action).unlink()
    else:
        journal._intent_path(action).write_text("{}", encoding="utf-8")
    before = snapshot(tmp_path)
    scenario.plane.recover(scenario.objective_id, dry_run=True)
    assert snapshot(tmp_path) == before
    result = finish(launch(tmp_path, "recover"))
    if damage == "projections":
        assert result["execution_status"] == "COMPLETED", result
        assert result["recovered"] is True
        scenario.freeze()
        scenario.plane.tick(scenario.objective_id)
        scenario.confirm()
    else:
        assert result["execution_status"] == "DENY", result
        assert snapshot(tmp_path) == before


@pytest.mark.parametrize("contender", ["tick", "hold_domain"])
def test_automatic_and_manual_proposal_share_process_lock(tmp_path, contender):
    scenario = Scenario(tmp_path).initialize()
    scenario.design()
    scenario.approve()
    owner = launch(tmp_path, "hold_tick" if contender == "tick" else "hold_domain")
    try:
        assert owner.stdout.readline().strip() == "LOCKED"
        denied = finish(launch(tmp_path, "tick"))
        assert denied["stop_reason"] == "CONTROL_PLANE_CONCURRENT_RUN", denied
        finish(owner, command="GO\n")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.communicate(timeout=10)
    assert len(list(tmp_path.rglob("CANDIDATE_PROPOSAL.json"))) == 1
    scenario.freeze()
    scenario.plane.tick(scenario.objective_id)
    scenario.confirm()


@pytest.mark.parametrize("boundary", ["exhausted", "reserved", "ambiguous"])
def test_budget_boundary_blocks_automatic_progress_without_mutation(tmp_path, boundary):
    scenario = Scenario(tmp_path).initialize()
    registry = SearchBudgetRegistryV1(scenario.objective_id, scenario.budget_path)
    if boundary == "exhausted":
        registry.consume(registry.reserve("objective", scenario.objective_id, 4))
    elif boundary == "reserved":
        registry.reserve("objective", scenario.objective_id, 4)
    else:
        other = tmp_path / "data/research/research_factory/batches/OTHER/search_budget_registry.json"
        other.parent.mkdir(parents=True)
        other.write_bytes(scenario.budget_path.read_bytes())
    before = snapshot(tmp_path)
    scenario.plane.tick(scenario.objective_id, dry_run=True)
    assert snapshot(tmp_path) == before
    result = scenario.plane.tick(scenario.objective_id)
    assert result["decision"]["automatic_execution"] is False, result
    assert result["decision"]["current_effective_state"] in {"BUDGET_EXHAUSTED", "BUDGET_AUTHORITY_AMBIGUOUS"}, result
    after = snapshot(tmp_path)
    assert all(after[name] == value for name, value in before.items())
    assert not list(tmp_path.rglob("CANDIDATE_PROPOSAL.json"))
    assert not list(tmp_path.rglob("factory_trial_ledger.json"))


@pytest.mark.parametrize("authorized", [False, True])
def test_delete_daemon_projection_preserves_canonical_structural_and_authorization(tmp_path, authorized):
    scenario = Scenario(tmp_path).initialize().ready()
    scenario.structural()
    if authorized:
        scenario.authorize()
    expected = scenario.plane.inspect(scenario.objective_id)
    paths = list(tmp_path.rglob("daemon_checkpoint.json"))
    assert paths
    for path in paths:
        path.unlink()
    before = snapshot(tmp_path)
    actual = scenario.plane.inspect(scenario.objective_id)
    assert actual["decision"]["selected_action"]["action_type"] == expected["decision"]["selected_action"]["action_type"]
    assert actual["authorization"]["authorized"] is authorized
    assert snapshot(tmp_path) == before
