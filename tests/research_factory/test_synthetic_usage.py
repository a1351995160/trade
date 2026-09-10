"""实际服务/API生成合成测试资格，不写成功回执、不替换权限判定。"""
from datetime import timedelta
import shutil

import pytest
from fastapi.testclient import TestClient

from test_engineering_workbench import workbench, GOVERNED, ENDPOINT
from test_daily_plan import timestamp
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.synthetic_usage import SyntheticUsageServiceV1, utc_now
from chanlun_trader.webapp import create_app


def operate(service, action, **payload):
    return SyntheticUsageServiceV1(service).perform(action, GOVERNED,
        {"confirmed": True, "context_hash": service.inspect()["context_hash"], **payload})


def request(service, purposes=None):
    return operate(service, "request", candidate_id=next(iter(service.sources)),
        purposes=purposes or ["DAILY_PLAN", "PAPER_REPLAY"], valid_until=(utc_now() + timedelta(hours=1)).isoformat())


def test_real_request_confirmation_replay_and_revocation(tmp_path):
    service = workbench(tmp_path)
    source = next(iter(service.sources.values()))
    candidate = source["contract"].candidate_id
    day = timestamp(source["inputs"]["exec_calendar"][0])
    pending = request(service)
    assert not service.preview(day)["source_plans"]
    assert SyntheticUsageServiceV1(service).active(candidate, "DAILY_PLAN") == []
    with TestClient(create_app(service.root, GOVERNED, engineering_workbench=service), base_url="http://127.0.0.1") as client:
        view = client.get(ENDPOINT).json()
        confirmed = client.post(ENDPOINT + "/usage/confirm", json={"confirmed": True,
            "context_hash": view["context_hash"], "request_id": pending["request_id"]})
        assert confirmed.status_code == 200, confirmed.text
        view = client.get(ENDPOINT).json()
        assert view["synthetic_qualified_strategy_count"] == 1 and view["qualified_strategy_count"] == 0
        plan = client.get(ENDPOINT, params={"plan_at": str(day)}).json()
        assert plan["source_plans"] and plan["plan_mode"] == "SYNTHETIC_QUALIFIED_TEST"
        assert plan["execution_ready"] is False
        replay = client.post(ENDPOINT + "/advance", json={"confirmed": True, "context_hash": view["context_hash"],
            "candidate_id": candidate, "event_count": 9})
        assert replay.status_code == 200 and replay.json()["state"]["trades"]
        assert (service.output_root / replay.json()["usage_evidence_ref"] / "result.json").is_file()
    operate(service, "revoke", request_id=pending["request_id"])
    assert not service.preview(day)["source_plans"]
    with pytest.raises(ValueError, match="QUALIFICATION_REQUIRED"):
        service.advance(GOVERNED, {"confirmed": True, "context_hash": service.inspect()["context_hash"],
            "candidate_id": candidate, "event_count": 10})
    assert service.inspect()["paper"][candidate]["completed_events"] == 9


@pytest.mark.parametrize("defect", ["readonly", "confirmation", "stale", "unknown", "expired", "purpose"])
def test_qualification_rejects_invalid_authority_and_request(tmp_path, defect):
    service = workbench(tmp_path)
    payload = {"confirmed": True, "context_hash": service.inspect()["context_hash"],
        "candidate_id": next(iter(service.sources)), "purposes": ["DAILY_PLAN"],
        "valid_until": (utc_now() + timedelta(hours=1)).isoformat()}
    policy = GOVERNED
    if defect == "readonly":
        policy = ExecutionPolicy()
    elif defect == "confirmation":
        payload["confirmed"] = False
    elif defect == "stale":
        payload["context_hash"] = "OLD"
    elif defect == "unknown":
        payload["candidate_id"] = "UNKNOWN"
    elif defect == "expired":
        payload["valid_until"] = "2020-01-01T00:00:00+00:00"
    else:
        payload["purposes"] = ["REAL_ORDER"]
    with pytest.raises((ValueError, PermissionError)):
        SyntheticUsageServiceV1(service).perform("request", policy, payload)
    assert not list(service.output_root.rglob("request.json"))


def test_purpose_and_expiry_do_not_fall_back_to_unqualified_preview(tmp_path, monkeypatch):
    service = workbench(tmp_path)
    pending = request(service, ["DAILY_PLAN"])
    operate(service, "confirm", request_id=pending["request_id"])
    candidate = next(iter(service.sources))
    with pytest.raises(ValueError, match="QUALIFICATION_REQUIRED"):
        service.advance(GOVERNED, {"confirmed": True, "context_hash": service.inspect()["context_hash"],
            "candidate_id": candidate, "event_count": 1})
    later = utc_now() + timedelta(hours=2)
    monkeypatch.setattr("chanlun_trader.research_factory.synthetic_usage.utc_now", lambda: later)
    state = service.inspect()
    assert state["synthetic_usage"]["configured"]
    assert state["synthetic_qualified_strategy_count"] == 0
    assert state["synthetic_usage"]["records"][0]["status"] == "EXPIRED"


def test_two_actual_qualified_sources_share_cash_and_cold_file_assembly(tmp_path):
    from test_portfolio_plan import setup
    from chanlun_trader.research_factory.engineering_workbench import EngineeringWorkbenchV1
    from chanlun_trader.research_factory.engineering_workspace import save_engineering_workspace, load_engineering_workspace
    root = tmp_path / "inputs"
    sources, policy, ledger = setup(root)
    for index, source in enumerate(sources.values()):
        source["root"] = root / str(index)
    service = EngineeringWorkbenchV1(root, sources, ledger, policy, tmp_path / "output")
    for candidate in sources:
        pending = operate(service, "request", candidate_id=candidate, purposes=["DAILY_PLAN", "PAPER_REPLAY"],
            valid_until=(utc_now() + timedelta(hours=1)).isoformat())
        operate(service, "confirm", request_id=pending["request_id"])
    day = timestamp(next(iter(sources.values()))["inputs"]["exec_calendar"][0])
    plan = service.preview(day)
    assert len(plan["source_plans"]) == 2
    buys = [item for item in plan["entries"] if item["quantity"]]
    assert buys and len({item["symbol"] for item in buys}) == len(buys)
    assert sum(item["quantity"] * item["reference_price"] + item["estimated_fee"] for item in buys) <= ledger.cash
    path = tmp_path / "workbench.json"
    save_engineering_workspace(path, service)
    restored = load_engineering_workspace(path)
    assert restored.inspect()["synthetic_qualified_strategy_count"] == 2
    assert restored.preview(day) == plan
    assert restored.inspect()["qualified_strategy_count"] == 0


def test_copied_evidence_cannot_authorize_another_workspace(tmp_path):
    first, second = workbench(tmp_path / "one"), workbench(tmp_path / "two")
    pending = request(first)
    operate(first, "confirm", request_id=pending["request_id"])
    shutil.copytree(first.output_root / "synthetic-usage", second.output_root / "synthetic-usage")
    assert second.inspect()["synthetic_usage"]["records"][0]["status"] == "BINDING_CHANGED"
    assert second.inspect()["synthetic_qualified_strategy_count"] == 0


def test_unrecorded_or_damaged_confirmation_is_not_authority(tmp_path):
    service = workbench(tmp_path)
    with pytest.raises(OSError):
        operate(service, "confirm", request_id="SYNTHETIC_USAGE_" + "a" * 64)
    pending = request(service)
    operate(service, "confirm", request_id=pending["request_id"])
    operate(service, "confirm", request_id=pending["request_id"])
    assert service.inspect()["synthetic_qualified_strategy_count"] == 1
    path = next(service.output_root.rglob("confirmation.json"))
    path.write_text(path.read_text(encoding="utf-8").replace('"confirmed":true', '"confirmed":false'), encoding="utf-8")
    with pytest.raises(ValueError, match="EVIDENCE_DAMAGED"):
        service.inspect()


def test_removed_member_keeps_history_but_cannot_keep_qualification(tmp_path):
    service = workbench(tmp_path)
    pending = request(service)
    operate(service, "confirm", request_id=pending["request_id"])
    service.sources.clear()
    state = service.inspect()
    assert state["synthetic_qualified_strategy_count"] == 0
    assert state["synthetic_usage"]["records"][0]["status"] == "BINDING_CHANGED"


def test_publish_rechecks_actual_input_files_after_qualification(tmp_path):
    import pandas as pd
    service = workbench(tmp_path)
    pending = request(service)
    operate(service, "confirm", request_id=pending["request_id"])
    source = next(iter(service.sources.values()))
    cache = source["root"] / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet"
    frame = pd.read_parquet(cache)
    frame.iloc[0, frame.columns.get_loc("available_at")] = None
    frame.to_parquet(cache, index=False)
    with pytest.raises(ValueError):
        service.publish(GOVERNED, {"confirmed": True, "context_hash": service.inspect()["context_hash"],
            "plan_at": timestamp(source["inputs"]["exec_calendar"][0])})
    assert not list((service.output_root / "portfolio").glob("*.json"))


def test_expiry_during_real_input_validation_never_persists_a_request(tmp_path, monkeypatch):
    service = workbench(tmp_path)
    now = utc_now()
    ticks = iter((now, now + timedelta(hours=2)))
    monkeypatch.setattr("chanlun_trader.research_factory.synthetic_usage.utc_now", lambda: next(ticks))
    with pytest.raises(ValueError, match="SYNTHETIC_USAGE_EXPIRED"):
        operate(service, "request", candidate_id=next(iter(service.sources)), purposes=["DAILY_PLAN"],
            valid_until=(now + timedelta(hours=1)).isoformat())
    assert not list(service.output_root.rglob("request.json"))


def test_lost_qualification_history_never_restores_unqualified_replay(tmp_path):
    service = workbench(tmp_path)
    pending = request(service)
    operate(service, "confirm", request_id=pending["request_id"])
    history = service.output_root / "synthetic-usage"
    preserved = service.output_root / "preserved-usage-history"
    assert history.resolve().is_relative_to(tmp_path.resolve()) and preserved.resolve().is_relative_to(tmp_path.resolve())
    history.rename(preserved)
    with pytest.raises(ValueError, match="QUALIFICATION_REQUIRED"):
        service.advance(GOVERNED, {"confirmed": True, "context_hash": service.inspect()["context_hash"],
            "candidate_id": next(iter(service.sources)), "event_count": 1})
    assert service.inspect()["synthetic_usage"]["configured"] is True
    assert not list(service.output_root.rglob("header.json"))


def test_revocation_loss_is_blocked_by_actual_cold_consumers(tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    from chanlun_trader.research_factory.engineering_workspace import save_engineering_workspace
    from chanlun_trader.research_factory.source_dependencies import SOURCE_ROOT

    service = workbench(tmp_path)
    config = save_engineering_workspace(tmp_path / "workbench.json", service)
    pending = request(service)
    operate(service, "confirm", request_id=pending["request_id"])
    candidate = next(iter(service.sources))
    assert service.advance(GOVERNED, {"confirmed": True, "context_hash": service.inspect()["context_hash"],
        "candidate_id": candidate, "event_count": 9})["completed_events"] == 9
    operate(service, "revoke", request_id=pending["request_id"])
    assert SyntheticUsageServiceV1(service).inspect()["records"][0]["status"] == "REVOKED"
    directory = service.output_root / "synthetic-usage" / pending["request_id"]
    preserved = directory / "preserved-revocation.bin"
    (directory / "revocation.json").rename(preserved)
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SOURCE_ROOT / "tests/isolation"), str(SOURCE_ROOT / "src")]))
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("usage_integrity_worker.py")), str(config)],
        cwd=tmp_path, env=environment, capture_output=True, timeout=60)
    evidence = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / "ca01-revocation-loss"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "stdout.bin").write_bytes(result.stdout)
    (evidence / "stderr.bin").write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    values = json.loads(result.stdout)
    assert all(item["blocked"] for item in values.values()), values


@pytest.mark.parametrize("defect", ["head_missing", "head_corrupt", "confirmation_missing", "revocation_missing", "old_head"])
def test_committed_usage_damage_blocks_api_and_does_not_repair(tmp_path, defect):
    service = workbench(tmp_path)
    pending = request(service)
    operate(service, "confirm", request_id=pending["request_id"])
    directory = service.output_root / "synthetic-usage" / pending["request_id"]
    active_head = (directory / "head.json").read_bytes()
    operate(service, "revoke", request_id=pending["request_id"])
    operate(service, "revoke", request_id=pending["request_id"])
    if defect.endswith("_missing"):
        (directory / (defect.removesuffix("_missing") + ".json")).rename(directory / "preserved.bin")
    else:
        (directory / "head.json").write_bytes(active_head if defect == "old_head" else b"{}")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    with TestClient(create_app(service.root, GOVERNED, engineering_workbench=service), base_url="http://127.0.0.1") as client:
        assert client.get(ENDPOINT).status_code == 409
        for action in ("publish", "advance", "usage/confirm"):
            assert client.post(ENDPOINT + "/" + action, json={"confirmed": True, "context_hash": "stale",
                "request_id": pending["request_id"]}).status_code == 409
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}


@pytest.mark.parametrize("action", ["confirm", "revoke"])
def test_hard_exit_after_usage_event_never_rolls_back_to_active(tmp_path, action):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    from chanlun_trader.research_factory.engineering_workspace import save_engineering_workspace
    from chanlun_trader.research_factory.source_dependencies import SOURCE_ROOT
    service = workbench(tmp_path)
    config = save_engineering_workspace(tmp_path / "workbench.json", service)
    pending = request(service)
    if action == "revoke":
        operate(service, "confirm", request_id=pending["request_id"])
    directory = service.output_root / "synthetic-usage" / pending["request_id"]
    head = (directory / "head.json").read_bytes()
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SOURCE_ROOT / "tests/isolation"), str(SOURCE_ROOT / "src")]))
    command = [sys.executable, str(Path(__file__).with_name("usage_integrity_worker.py")), str(config)]
    evidence = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / ("ca01-crash-" + action)
    evidence.mkdir(parents=True, exist_ok=True)
    crash = subprocess.run([*command, action], cwd=tmp_path, env=environment, capture_output=True, timeout=60)
    (evidence / "crash-stdout.bin").write_bytes(crash.stdout)
    (evidence / "crash-stderr.bin").write_bytes(crash.stderr)
    assert crash.returncode == 73
    assert (directory / ("confirmation.json" if action == "confirm" else "revocation.json")).exists()
    assert (directory / "head.json").read_bytes() == head
    restored = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, timeout=60)
    (evidence / "restarted-stdout.bin").write_bytes(restored.stdout)
    (evidence / "restarted-stderr.bin").write_bytes(restored.stderr)
    assert restored.returncode == 0, restored.stderr.decode("utf-8")
    assert all(value["blocked"] for value in json.loads(restored.stdout).values())
    assert (directory / "head.json").read_bytes() == head


def test_actual_legacy_receipts_are_read_only_without_silent_migration(tmp_path):
    import hashlib
    import json
    from pathlib import Path
    fixture = Path(__file__).with_name("fixtures") / "ca01_legacy_usage"
    provenance = json.loads((fixture / "provenance.json").read_text(encoding="utf-8"))
    service = workbench(tmp_path)
    old_id = Path(provenance["source_path"]).name
    directory = service.output_root / "synthetic-usage" / old_id
    directory.mkdir(parents=True)
    for item in provenance["files"]:
        data = (fixture / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        (directory / item["path"]).write_bytes(data)
    assert SyntheticUsageServiceV1(service).inspect()["records"][0]["status"] == "LEGACY_REVOKED"
    (directory / "revocation.json").rename(directory / "preserved-revocation.bin")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert SyntheticUsageServiceV1(service).inspect()["records"][0]["status"] == "LEGACY_UNVERIFIED"
    assert service.inspect()["synthetic_qualified_strategy_count"] == 0
    with pytest.raises(ValueError, match="NOT_CONFIRMABLE"):
        operate(service, "confirm", request_id=old_id)
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    current = request(service)
    operate(service, "confirm", request_id=current["request_id"])
    assert service.inspect()["synthetic_qualified_strategy_count"] == 1


def test_revoke_and_actual_paper_use_share_existing_mutation_lock(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from chanlun_trader.research_factory.paper_replay import PaperReplaySessionV1
    from chanlun_trader.research_factory.mutation_boundary import MutationBusyError
    service = workbench(tmp_path)
    pending = request(service)
    operate(service, "confirm", request_id=pending["request_id"])
    candidate = next(iter(service.sources))
    entered, proceed = Event(), Event()
    original = PaperReplaySessionV1.advance
    def pause_at_use(session, count):
        entered.set()
        assert proceed.wait(10)
        return original(session, count)
    monkeypatch.setattr(PaperReplaySessionV1, "advance", pause_at_use)
    payload = {"confirmed": True, "context_hash": service.inspect()["context_hash"],
        "candidate_id": candidate, "event_count": 9}
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(service.advance, GOVERNED, payload)
        try:
            assert entered.wait(10)
            with pytest.raises(MutationBusyError):
                operate(service, "revoke", request_id=pending["request_id"])
        finally:
            proceed.set()
        assert running.result()["completed_events"] == 9
    operate(service, "revoke", request_id=pending["request_id"])
    with pytest.raises(ValueError, match="QUALIFICATION_REQUIRED"):
        service.advance(GOVERNED, {**payload, "context_hash": service.inspect()["context_hash"], "event_count": 10})
    assert service.inspect()["paper"][candidate]["completed_events"] == 9
