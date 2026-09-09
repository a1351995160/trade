"""真实生命周期的 L1—L10 重启矩阵及执行证据边界。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from p3c_scenario import Scenario
from test_restart_recovery_v1 import launch as cp_launch, snapshot


def finish(process, expected=0):
    stdout, stderr = process.communicate(timeout=30)
    # 证据目录由测试控制者指定，独立于被 snapshot 检查的研究输入。
    evidence_root = os.environ.get("CHANLUN_PROCESS_EVIDENCE_DIR")
    if evidence_root:
        root = Path(evidence_root)
        root.mkdir(parents=True, exist_ok=True)
        evidence = Path(tempfile.mkdtemp(prefix=f"child-{process.pid}-", dir=root))
        (evidence / "stdout.bin").write_bytes(stdout if isinstance(stdout, bytes) else stdout.encode("utf-8"))
        (evidence / "stderr.bin").write_bytes(stderr if isinstance(stderr, bytes) else stderr.encode("utf-8"))
        (evidence / "result.json").write_text(json.dumps({
            "pid": process.pid, "returncode": process.returncode, "expected": expected,
            "test": os.environ.get("PYTEST_CURRENT_TEST"),
            "operation": process.args[-1] if "-c" not in process.args else "<INLINE_SYNTHETIC_PROBE>",
            "stream_capture": "RAW_BYTES" if isinstance(stderr, bytes) else "P3B_DECODED_TEXT_UTF8",
        }), encoding="utf-8")
    if isinstance(stdout, bytes):
        stdout, stderr = stdout.decode("utf-8"), stderr.decode("utf-8")
    assert process.returncode == expected, (stdout, stderr)
    probes = [line for line in stderr.splitlines() if line.startswith(("P3C_WORKER_PROBES=", "P3A_PROCESS_PROBES="))]
    if expected == 0:
        assert probes, stderr
    for line in probes:
        report = json.loads(line.split("=", 1)[1])
        counts = [count for group in report.values() for count in group.values()] if "executors" in report else list(report.values())
        assert all(count == 0 for count in counts), report
    print(json.dumps({"worker_exit": expected, "probe_checkpoints": probes or "UNAVAILABLE_P3B_HARD_EXIT_USE_FSYNC_EVENTS"}))
    return json.loads(stdout.splitlines()[-1]) if stdout.strip() else None


def launch(scenario, operation):
    return subprocess.Popen([sys.executable, str(Path(__file__).with_name("p3c_process_worker.py")), str(scenario.root), scenario.objective_id, operation], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def selected(result):
    return result["decision"]["selected_action"]["action_type"]


@pytest.mark.parametrize("level", range(1, 11), ids=lambda level: f"L{level}")
def test_lifecycle_restart_matrix(tmp_path, level):
    scenario = Scenario(tmp_path).initialize()
    scenario.design()
    scenario.approve()
    budget = scenario.budget_path.read_bytes()
    if level == 2:
        finish(cp_launch(tmp_path, "exit_after"), expected=92)
        assert finish(cp_launch(tmp_path, "tick"))["execution"]["recovered"] is True
    elif level >= 3:
        assert scenario.plane.tick(scenario.objective_id)["execution"]["execution_status"] == "COMPLETED"
    if level >= 3:
        scenario.freeze()
    if level == 4:
        finish(cp_launch(tmp_path, "exit_after"), expected=92)
        assert finish(cp_launch(tmp_path, "tick"))["execution"]["recovered"] is True
    elif level >= 5:
        assert scenario.plane.tick(scenario.objective_id)["execution"]["execution_status"] == "COMPLETED"
    if level in {5, 6}:
        finish(launch(scenario, "confirmation_exit" if level == 5 else "durable_exit"), expected=94)
    elif level >= 7:
        scenario.confirm()
    if level == 8:
        finish(launch(scenario, "structural_exit"), expected=95)
    elif level >= 9:
        scenario.structural()
    if level == 10:
        scenario.authorize()

    before = snapshot(tmp_path)
    state = finish(launch(scenario, "inspect"))
    assert snapshot(tmp_path) == before
    assert selected(finish(launch(scenario, "dry_run"))) == selected(state)
    assert snapshot(tmp_path) == before
    expected = {1: "GENERATE_CANDIDATE_PROPOSAL", 2: "WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE", 3: "CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW", 4: "WAIT_FOR_EXECUTABLE_MATERIALIZATION_CONFIRMATION", 5: "RECOVER_EXECUTABLE_MATERIALIZATION", 6: "RUN_STRUCTURAL_PREFLIGHT", 7: "RUN_STRUCTURAL_PREFLIGHT", 8: "WAIT_FOR_PREDICTIVE_AUTHORIZATION", 9: "WAIT_FOR_PREDICTIVE_AUTHORIZATION", 10: "START_PREDICTIVE_TRIAL"}
    assert selected(state) == expected[level], state
    immutable = {p: p.read_bytes() for p in tmp_path.rglob("*.json") if p.name in {"AI_RESEARCH_DESIGN_PROPOSAL.json", "AI_DESIGN_APPROVAL_RECEIPT.json", "CANDIDATE_PROPOSAL.json", "CANDIDATE_FREEZE_RECEIPT.json", "EXECUTABLE_MATERIALIZATION_CONFIRMATION.json", "durable_frozen_candidate_contracts.json"}}
    assert {"AI_RESEARCH_DESIGN_PROPOSAL.json", "AI_DESIGN_APPROVAL_RECEIPT.json"} <= {p.name for p in immutable}
    if level == 1:
        finish(cp_launch(tmp_path, "tick"))
    if level <= 2:
        finish(launch(scenario, "freeze"))
    if level <= 3:
        finish(cp_launch(tmp_path, "tick"))
    if level <= 4:
        finish(launch(scenario, "confirm"))
    elif level == 5:
        recovered = finish(cp_launch(tmp_path, "tick"))
        assert recovered["execution"]["execution_status"] == "COMPLETED", recovered
    if level <= 7:
        finish(launch(scenario, "structural"))
    if level <= 9:
        finish(launch(scenario, "authorize"))
    final = finish(cp_launch(tmp_path, "tick"))
    assert final["decision"]["permission"]["reason_code"] == "PHASE2_PREDICTIVE_EXECUTION_DISABLED"
    assert final["authorization"]["authorized"] is True
    assert scenario.budget_path.read_bytes() == budget
    assert all(path.read_bytes() == content for path, content in immutable.items())
    events = [json.loads(line) for line in (tmp_path / "p3c-test-events.jsonl").read_text().splitlines()]
    assert sum(row["event"] == "synthetic_design_call" for row in events) == 1
    assert sum(row["event"] == "synthetic_structural_provider" for row in events) == 1
    assert not list(tmp_path.rglob("factory_trial_ledger.json"))
    print(json.dumps({"level": level, "restart_action": selected(state), "final_action": selected(final), "events": events}, ensure_ascii=True))


@pytest.mark.parametrize("operation", ["exit_before", "exit_retry_marker"])
def test_full_semantic_public_retry_keeps_attempt_identity(tmp_path, operation):
    scenario = Scenario(tmp_path).initialize()
    scenario.design()
    scenario.approve()
    if operation == "exit_retry_marker":
        failed = finish(cp_launch(tmp_path, "fail_before"))
        assert failed["execution"]["execution_status"] == "FAILED"
        finish(cp_launch(tmp_path, operation), expected=93)
    else:
        finish(cp_launch(tmp_path, operation), expected=91)
    result = finish(cp_launch(tmp_path, "recover"))
    assert result["execution_status"] == "COMPLETED", result
    rows = scenario.plane._journal(scenario.objective_id).read()
    assert rows[-1].attempt == 2
    assert len({(row.action_id, row.idempotency_key, row.execution_intent_hash) for row in rows}) == 1
    scenario.freeze()
    scenario.plane.tick(scenario.objective_id)
    scenario.confirm()
    assert selected(scenario.plane.inspect(scenario.objective_id)) == "RUN_STRUCTURAL_PREFLIGHT"
