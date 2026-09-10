"""真实引擎逐事件Paper回放与完整回测对账；全部现场合成输入。"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from r1_caller_fixture import fixture
from test_r1_batch_caller_inputs import prepare
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.paper_replay import PaperReplaySessionV1, read_paper_archive


def setup(root):
    case = fixture(root / "inputs")
    inputs = prepare(case)
    caller, policy, contract, record, _ = case
    def session():
        return PaperReplaySessionV1(caller.root, root / "paper", record, contract, policy, inputs)
    return case, inputs, session


def test_replay_restart_and_duplicate_match_full_engine(tmp_path):
    case, inputs, create = setup(tmp_path)
    session = create()
    assert not (tmp_path / "paper").exists()
    first = session.advance(9)
    assert first["completed_events"] == 9
    archive = read_paper_archive(tmp_path / "paper")
    assert archive["status"] == "HISTORICAL_REPLAY"
    assert stable_hash(archive["state"]) == stable_hash(first["state"])
    assert session.advance(9) == first
    recovered = create()
    assert recovered.status() == first
    completed = recovered.advance(len(recovered.engine.clock.events))
    assert completed["status"] == "REPLAY_COMPLETE"
    assert completed["real_observation_days"] == 0
    caller, policy, _, record, _ = case
    reference = caller._invoke_runner(policy, record, "D2_REFERENCE", inputs, portfolio_name="BASE_RESEARCH")["engine"]
    assert completed["state"]["cash"] == reference.ledger.cash
    assert completed["state"]["fees"] == reference.ledger.total_fees
    assert completed["state"]["trades"] == [asdict(value) for value in reference.ledger.trades]
    assert completed["state"]["lots"] == {key: asdict(value) for key, value in reference.ledger.lots.items()}
    assert completed["state"]["orders"] == {key: asdict(value) for key, value in reference.orders.orders.items()}
    assert completed["state"]["event_hash"] == stable_hash(reference.event_log.to_records())
    assert create().status() == completed
    assert any(json.loads(path.read_text())["plan"] is not None for path in (tmp_path / "paper/events").glob("*.json"))


def test_changed_input_is_not_resumed_under_new_identity(tmp_path):
    _, inputs, create = setup(tmp_path)
    create().advance(1)
    inputs["input_diagnostics"]["input_identity"] = "CHANGED"
    with pytest.raises(ValueError, match="PAPER_INPUT_OR_SOURCE_CHANGED"):
        create()


@pytest.mark.parametrize("mode", ["corrupt", "gap"])
def test_corrupt_or_missing_history_blocks_recovery(tmp_path, mode):
    _, _, create = setup(tmp_path)
    create().advance(3)
    path = tmp_path / "paper/events/00000001.json"
    if mode == "gap":
        path.unlink()
    else:
        payload = json.loads(path.read_text())
        payload["state_hash"] = "WRONG"
        path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="PAPER_REPLAY_RECONCILIATION_FAILED|PAPER_EVENT_SEQUENCE_GAP"):
        create()
    with pytest.raises(ValueError, match="PAPER_ARCHIVE_HASH_CONFLICT|PAPER_EVENT_SEQUENCE_GAP"):
        read_paper_archive(tmp_path / "paper")


def test_failed_write_requires_reopen_and_replays_only_committed_events(tmp_path, monkeypatch):
    _, _, create = setup(tmp_path)
    session = create()
    session.advance(1)
    from chanlun_trader.research_factory import paper_replay
    original = paper_replay._immutable
    def fail(path, value):
        if path.name == "00000001.json":
            raise OSError("synthetic output unavailable")
        original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(paper_replay, "_immutable", fail)
        with pytest.raises(OSError):
            session.advance(2)
        with pytest.raises(ValueError, match="PAPER_RECOVERY_REQUIRED"):
            session.advance(3)
    restored = create()
    assert restored.completed_events == 1
    assert restored.advance(2)["completed_events"] == 2


def test_real_process_exit_and_recovery_preserve_same_paper_session(tmp_path):
    case, _, create = setup(tmp_path)
    caller, _, contract, _, _ = case
    (caller.root / "caller-contract.json").write_text(json.dumps(contract.to_dict()), encoding="utf-8")
    before = {str(path): path.read_bytes() for path in caller.root.rglob("*") if path.is_file()}
    from chanlun_trader.research_factory.source_dependencies import SOURCE_ROOT
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SOURCE_ROOT / "tests/isolation"), str(SOURCE_ROOT / "src")]))
    evidence = Path(os.environ.get("CHANLUN_PROCESS_EVIDENCE_DIR", str(tmp_path / "evidence"))) / "paper-replay"
    evidence.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__).with_name("paper_replay_worker.py")),
               str(caller.root), str(tmp_path / "paper"), caller.objective_id]
    for mode, expected in (("interrupt", 73), ("resume", 0)):
        try:
            result = subprocess.run([*command, mode], cwd=tmp_path, env=environment, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            (evidence / f"{mode}-stdout.bin").write_bytes(exc.stdout or b"")
            (evidence / f"{mode}-stderr.bin").write_bytes(exc.stderr or b"")
            raise
        (evidence / f"{mode}-stdout.bin").write_bytes(result.stdout)
        (evidence / f"{mode}-stderr.bin").write_bytes(result.stderr)
        assert result.returncode == expected, result.stderr.decode("utf-8")
        if mode == "interrupt":
            assert b"PAPER_COMMITTED_BEFORE_EXIT=9" in result.stdout
        else:
            completed = json.loads(result.stdout)
            assert completed["status"] == "REPLAY_COMPLETE"
            assert completed["real_observation_days"] == 0
            assert stable_hash(completed) == stable_hash(create().status())
    assert before == {str(path): path.read_bytes() for path in caller.root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("scenario", ["partial", "limit_up"])
def test_actual_broker_partial_fill_and_rejection(tmp_path, scenario):
    case = fixture(tmp_path / "inputs")
    caller, policy, contract, record, cache = case
    path = caller.root / "data/research/daily_all.parquet"
    daily = pd.read_parquet(path)
    factors = pd.read_parquet(cache)
    days = sorted(factors.date.unique())
    if scenario == "partial":
        # BUY既有合同在成交时重设数量；验证真实SELL余量生命周期。
        exit_day = days[1 + record.candidate.holding_period]
        daily.loc[daily.date >= exit_day, "volume"] = 1000.
        daily.loc[daily.date >= exit_day, "amount"] = 10000.
        factors.loc[factors.date >= exit_day, "volume"] = 1000.
        factors.loc[factors.date >= exit_day, "VOLUME_ACCEL"] = 1000.
    else:
        next_day = days[1]
        daily.loc[daily.date == next_day, ["open", "high", "low", "close"]] = 11.
    daily.to_parquet(path, index=False)
    factors.to_parquet(cache, index=False)
    inputs = prepare(case)
    session = PaperReplaySessionV1(caller.root, tmp_path / "paper", record, contract, policy, inputs)
    result = session.advance((2 + record.candidate.holding_period) * 4 + 1 if scenario == "partial" else 5)
    statuses = {item["status"].value for item in result["state"]["orders"].values()}
    if scenario == "partial":
        assert "PARTIALLY_FILLED" in statuses
        sells = [item for item in result["state"]["trades"] if item["side"].value == "SELL"]
        assert sells and all(item["quantity"] == 100 for item in sells)
    else:
        assert "REJECTED" in statuses
        assert not result["state"]["trades"]
