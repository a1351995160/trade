"""正式 reader、准备、部署 runner 与结果交接的合成验证。"""
from dataclasses import replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess

import pandas as pd
import pytest

from r1_caller_fixture import fixture
from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractV1
from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1
from chanlun_trader.research.validation_policy_v2 import load_validation_decision_policy_v2


@pytest.fixture(scope="module", autouse=True)
def engine_counts():
    previous = sys.getprofile()
    counts = {"synthetic_engine_run": 0, "forbidden": 0}
    def observe(frame, event, arg):
        if event == "call":
            module, name = frame.f_globals.get("__name__", ""), frame.f_code.co_name
            if module == "chanlun_trader.engine.engine" and name == "run":
                counts["synthetic_engine_run"] += 1
            if ((module.endswith("predictive_executor") and name == "execute")
                    or (module.endswith("trial_adapter") and name == "mark_performance_accessed")):
                counts["forbidden"] += 1
                raise AssertionError("R1_FORBIDDEN_FORMAL_PATH")
        if previous:
            previous(frame, event, arg)
    sys.setprofile(observe)
    yield counts
    sys.setprofile(previous)
    print("R1_CALLER_ENGINE_COUNTS=" + json.dumps(counts))
    assert counts["forbidden"] == 0


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("caller-template")
    caller, policy, contract, record, cache = fixture(root)
    return root, contract.to_dict(), caller.objective_id


@pytest.fixture
def case(tmp_path, template):
    source, payload, objective = template
    root = tmp_path / "input"
    shutil.copytree(source, root)
    contract = DurableFrozenCandidateContractV1.from_dict(payload)
    policy, _ = load_validation_decision_policy_v2(root / "data/research/strategy_validation/validation_decision_policy_v2.json")
    return CanonicalPredictiveExecutorV1(root, objective), policy, contract, contract.reconstruct_candidate(), root / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet"


def prepare(case):
    caller, policy, contract, record, cache = case
    return caller._prepare_inputs(policy, record, caller._corrected_module(), cache, contract=contract)


def snapshot(root):
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def invoke(case, inputs, **kwargs):
    caller, policy, contract, record, cache = case
    return caller._invoke_runner(policy, record, "R1_SYNTHETIC_ONLY", inputs, portfolio_name="BASE_RESEARCH", **kwargs)


def test_files_to_runner_and_reference(case, tmp_path):
    caller, policy, contract, record, cache = case
    before = snapshot(caller.root)
    inputs = prepare(case)
    actual = invoke(case, inputs)
    corrected = caller._corrected_module()
    # 参考路径从同一现场文件独立组装，不能用实际准备结果作为理想输入。
    calendar = json.loads((caller.root / "data/research/security_state/raw/trade_calendar.json").read_bytes())["trade_dates"]
    daily = pd.read_parquet(caller.root / "data/research/daily_all.parquet")
    universe = corrected.legacy.load_universe_sets(caller.root, calendar, set(daily.symbol))
    engine, metrics, diag = corrected.run_corrected_candidate(caller.root, record, "R1_SYNTHETIC_ONLY",
        pd.read_parquet(cache), corrected.legacy.build_store(daily), calendar[2:], universe,
        corrected.legacy.PITStateMap(caller.root / "data/research/security_state/normalized", calendar[2:]),
        {}, {}, {}, policy, portfolio_name="BASE_RESEARCH")
    assert actual["metrics"] == metrics
    assert actual["diagnostics"] == diag
    assert actual["status"] == "DIAGNOSTIC_ONLY"
    assert actual["evidence_status"] == "UNMATERIALIZED"
    assert actual["metrics_ref"] is None
    assert actual["ready_for_real_trial"] is False
    assert {t.side.value for t in actual["engine"].ledger.valid_trades} == {"BUY", "SELL"}
    assert [corrected.corrected_order_record(o) for o in actual["engine"].orders.orders.values()] == [corrected.corrected_order_record(o) for o in engine.orders.orders.values()]
    assert [corrected.corrected_trade_record(t) for t in actual["engine"].ledger.valid_trades] == [corrected.corrected_trade_record(t) for t in engine.ledger.valid_trades]
    assert actual["engine"].ledger.lots == engine.ledger.lots
    assert [(e.lot_id, e.payload) for e in actual["engine"].event_log if e.event_type == "EXIT_DECISION"] == [(e.lot_id, e.payload) for e in engine.event_log if e.event_type == "EXIT_DECISION"]
    assert inputs["exec_calendar"] == calendar[2:]
    assert inputs["universe"] == universe
    for symbol in universe[calendar[2]]:
        pd.testing.assert_frame_equal(inputs["store"].daily_raw[symbol], corrected.legacy.build_store(daily).daily_raw[symbol])
    assert inputs["input_diagnostics"]["warmup_start"] == calendar[0]
    assert [r["requested"] for r in inputs["input_diagnostics"]["reads"]] == [[calendar[0], calendar[-1]], [calendar[2], calendar[-1]]]
    assert snapshot(caller.root) == before
    print("R1_CALLER_PARITY_IDENTITY=" + json.dumps({"contract_hash": contract.content_hash,
        "input_identity": inputs["input_diagnostics"]["input_identity"],
        "source_identity": metrics["source_identity"], "deterministic_comparison": "EXACT",
        "governance_and_input_snapshot_unchanged": True}))


def test_small_capital_uses_independent_existing_contract(case):
    caller, policy, contract, record, cache = case
    inputs = prepare(case)
    base = invoke(case, inputs)
    small = replace(policy, initial_cash=policy.small_capital_cash, max_positions=policy.small_capital_slots, lot_size=policy.small_capital_lot_size)
    result = caller._invoke_runner(small, record, "R1_SMALL", inputs, portfolio_name="SMALL_CAPITAL_10K")
    assert result["metrics"]["initial_cash"] == 10000.
    assert result["engine"].ledger is not base["engine"].ledger
    assert {t.side.value for t in result["engine"].ledger.valid_trades} == {"BUY", "SELL"}
    assert result["metrics"]["portfolio_id"] == "SMALL_CAPITAL_10K"


def test_held_structure_exit_from_formal_files(tmp_path):
    case = fixture(tmp_path, structure_exit=True)
    result = invoke(case, prepare(case))
    assert {t.side.value for t in result["engine"].ledger.valid_trades} == {"BUY", "SELL"}
    assert any(e.payload["reason_code"] == "EXIT_DUE_STRUCTURE_INVALIDATION"
        for e in result["engine"].event_log if e.event_type == "EXIT_DECISION")
    frame = pd.read_parquet(case[-1])
    frame.loc[frame.date >= 20250721, "available_at"] = ""
    frame.to_parquet(case[-1], index=False)
    with pytest.raises(ValueError, match="R1_CALLER_INVALID_FACTOR_AVAILABLE_AT"):
        prepare(case)


@pytest.mark.parametrize("value", [None, pd.NaT, float("nan"), "", " ", "NaT", "not-a-date"])
@pytest.mark.parametrize("mixed", [True, False])
def test_invalid_time_blocked(case, value, mixed):
    frame = pd.read_parquet(case[-1])
    frame["available_at"] = frame.available_at.astype(object)
    frame.loc[[0] if mixed else frame.index, "available_at"] = value
    frame.to_parquet(case[-1], index=False)
    before = snapshot(case[0].root)
    with pytest.raises(ValueError, match="R1_CALLER_INVALID_FACTOR_AVAILABLE_AT"):
        prepare(case)
    assert snapshot(case[0].root) == before


@pytest.mark.parametrize("kind", ["same-day", "history", "naive", "utc", "future"])
def test_valid_time_meaning_preserved(case, kind):
    frame = pd.read_parquet(case[-1])
    stamps = pd.to_datetime(frame.available_at)
    if kind == "history":
        stamps -= pd.Timedelta(days=1)
    elif kind == "naive":
        stamps = stamps.dt.tz_localize(None)
    elif kind == "utc":
        stamps = stamps.dt.tz_convert("UTC")
    elif kind == "future":
        stamps[:] = pd.Timestamp("2026-01-01", tz="Asia/Shanghai")
    frame["available_at"] = stamps
    frame.to_parquet(case[-1], index=False)
    inputs = prepare(case)
    pd.testing.assert_series_equal(inputs["factor_values"].available_at, pd.read_parquet(case[-1]).available_at)
    result = invoke(case, inputs)
    if kind == "future":
        assert result["status"] == "INSUFFICIENT_EXECUTION_EVIDENCE"
        assert not result["engine"].ledger.valid_trades
    else:
        assert {t.side.value for t in result["engine"].ledger.valid_trades} == {"BUY", "SELL"}


@pytest.mark.parametrize("fault", ["no-pit", "one-source", "day", "unknown", "conflict"])
def test_pit_is_not_assumed(case, fault):
    root = case[0].root / "data/research/security_state/normalized"
    path = root / "st_state/symbol=600000_SH.jsonl"
    if fault in {"no-pit", "one-source"}:
        for p in (root / "st_state").glob("*"):
            p.unlink()
        if fault == "no-pit":
            for p in (root / "suspension_state").glob("*"):
                p.unlink()
    elif fault == "day":
        path.write_text("\n".join(path.read_text().splitlines()[1:]), encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n" + json.dumps(dict(trade_date=20250717, status="UNKNOWN" if fault == "unknown" else "ST")))
    with pytest.raises(ValueError, match="R1_CALLER_PIT_NOT_EXPLICIT_NORMAL_TRADING"):
        prepare(case)


@pytest.mark.parametrize("fault", ["available_at", "factor", "duplicate", "missing-row", "volume"])
def test_factor_input_failure(case, fault):
    frame = pd.read_parquet(case[-1])
    if fault in {"available_at", "factor"}:
        frame = frame.drop(columns=["available_at" if fault == "available_at" else "VOLUME_ACCEL"])
    elif fault == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]])
    elif fault == "missing-row":
        frame = frame.iloc[1:]
    else:
        frame.loc[0, "volume"] = 123.
    frame.to_parquet(case[-1], index=False)
    with pytest.raises(ValueError, match="R1_CALLER_"):
        prepare(case)


@pytest.mark.parametrize("fault", ["unit", "zero", "ohlc", "duplicate", "missing-symbol", "missing-date", "available_at"])
def test_daily_input_failure(case, fault):
    path = case[0].root / "data/research/daily_all.parquet"
    frame = pd.read_parquet(path)
    if fault == "unit":
        frame.loc[0, "volume_unit"] = "LOT"
    elif fault == "zero":
        frame.loc[0, "low"] = 0.
    elif fault == "ohlc":
        frame.loc[0, "high"] = 1.
    elif fault == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]])
    elif fault == "missing-symbol":
        frame = frame[frame.symbol != "600000.SH"]
    elif fault == "missing-date":
        frame = frame[frame.date != 20250722]
    else:
        frame.loc[0, "available_at"] = ""
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="R1_CALLER_"):
        prepare(case)


@pytest.mark.parametrize("fault", ["calendar", "warmup", "policy", "registry", "record"])
def test_frozen_identity_and_calendar_failure(case, fault):
    caller, policy, contract, record, cache = case
    if fault in {"calendar", "warmup"}:
        path = caller.root / "data/research/security_state/raw/trade_calendar.json"
        payload = json.loads(path.read_bytes())
        payload["trade_dates"] = payload["trade_dates"][2:] if fault == "warmup" else list(reversed(payload["trade_dates"]))
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif fault == "policy":
        policy = replace(policy, research_start=20250718)
    elif fault == "registry":
        path = caller.root / "data/research/factor_library_v1/registry.json"
        path.write_text(path.read_text(encoding="utf-8").replace("SYNTHETIC_TEST_ONLY", "OTHER"), encoding="utf-8")
    else:
        record = replace(record, exit_predicate=replace(record.exit_predicate, explanation="different contract"))
    with pytest.raises(ValueError, match="R1_CALLER_"):
        prepare((caller, policy, contract, record, cache))


def test_evidence_materialized_only_when_explicit(case, tmp_path):
    inputs = prepare(case)
    before = snapshot(case[0].root)
    result = invoke(case, inputs, evidence_root=tmp_path / "evidence")
    assert result["evidence_status"] == "MATERIALIZED"
    saved = json.loads(Path(result["metrics_ref"]).read_bytes())
    assert saved["candidate_preregistration_hash"] == case[3].preregistration_hash
    assert (tmp_path / "evidence/fills.csv").is_file()
    assert (tmp_path / "evidence/lot_lifecycle.csv").is_file()
    assert snapshot(case[0].root) == before
    with pytest.raises(ValueError, match="R1_SEPARATE_EVIDENCE_ROOT_REQUIRED"):
        invoke(case, inputs, evidence_root=case[0].root / "evidence")


@pytest.mark.parametrize("fault", ["tuple", "metrics", "diagnostics", "identity", "source", "engine", "path", "null-metric", "null-diagnostic"])
def test_incomplete_result_rejected(case, fault):
    inputs = prepare(case)
    actual = invoke(case, inputs)
    engine, metrics, diag = actual["engine"], dict(actual["metrics"]), dict(actual["diagnostics"])
    if fault == "metrics":
        del metrics["closed_trade_count"]
    elif fault == "diagnostics":
        del diag["entry_signals"]
    elif fault == "identity":
        metrics["trial_id"] = "OTHER"
    elif fault == "source":
        metrics["source_identity"] = {}
    elif fault == "engine":
        engine = None
    elif fault == "path":
        metrics["evidence_dir"] = "/not-materialized"
    elif fault == "null-metric":
        metrics["net_return"] = None
    elif fault == "null-diagnostic":
        diag["entry_signals"] = None
    result = (engine, metrics) if fault == "tuple" else (engine, metrics, diag)
    with pytest.raises(ValueError, match="R1_CALLER_"):
        case[0]._adapt_result(result, case[3], "R1_SYNTHETIC_ONLY", "BASE_RESEARCH", inputs)


def test_cwd_and_input_root_independence(case, tmp_path, monkeypatch):
    first = prepare(case)
    target = tmp_path / "second-input"
    shutil.copytree(case[0].root, target)
    (target / "scripts").mkdir()
    for name in ("run_engine_corrected_phase4_v3", "run_automated_strategy_validation_v1_rerun_v2"):
        (target / "scripts" / (name + ".py")).write_text("raise AssertionError('DATA_SOURCE_LOADED')", encoding="utf-8")
    other = CanonicalPredictiveExecutorV1(target, case[0].objective_id)
    cache = target / case[-1].relative_to(case[0].root)
    frame = pd.read_parquet(cache)
    frame["VOLUME_ACCEL"] = -1.
    frame.to_parquet(cache, index=False)
    monkeypatch.chdir(target)
    pd.testing.assert_frame_equal(first["factor_values"], prepare(case)["factor_values"])
    second = prepare((other, *case[1:4], cache))
    assert second["factor_values"].VOLUME_ACCEL.eq(-1.).all()
    assert first["input_diagnostics"]["input_identity"] != second["input_diagnostics"]["input_identity"]
    assert invoke(case, first)["engine"].ledger.valid_trades
    assert not invoke((other, *case[1:4], cache), second)["engine"].ledger.valid_trades


def test_formal_execute_reuses_narrow_functions():
    # 只查部署文本；隔离器已拦截 execute，本测试不调用或解包该函数。
    source = Path(inspect.getfile(CanonicalPredictiveExecutorV1)).read_text(encoding="utf-8")
    body = source.split("    def execute(", 1)[1]
    assert "self._prepare_inputs(" in body
    assert body.count("self._invoke_runner(") == 2
    assert "corrected.run_corrected_candidate(" not in body
    assert 'metrics_ref=str(provisional_path.relative_to(self.root))' in body


@pytest.mark.parametrize("fault", ["price", "frequency", "shared-time", "benchmark"])
def test_unsupported_contract_fail_closed(case, fault):
    from chanlun_trader.research.strategy_candidate import StrategyCandidateSpec
    from chanlun_trader.research.strategy_semantic import build_semantic_record, _semantic_fingerprint
    caller, policy, contract, record, cache = case
    seed = record.candidate.to_dict()
    if fault == "price":
        seed["price_mode"] = "PIT_QFQ"
    elif fault == "frequency":
        seed["required_frequency"] = ["DAILY", "5M"]
    elif fault == "shared-time":
        seed["factor_bindings"] = [*seed["factor_bindings"], {"factor_id": "RETURN_5D", "role": "PRIMARY_ALPHA", "direction": "POSITIVE"}]
        seed["factor_roles"] = seed["factor_bindings"]
    hypothesis = {"hypothesis_id": record.candidate.parent_hypothesis_id, "hypothesis_fingerprint": contract.hypothesis_fingerprint,
        "hypothesis_type": "CONTINUATION", "market_regime": []}
    other = build_semantic_record(StrategyCandidateSpec.create(seed), hypothesis)
    if fault == "benchmark":
        other = replace(other, signal_predicate=replace(other.signal_predicate,
            regime_conditions=({"mode": "HARD_GATE", "allowed_values": ["BULL"]},)))
        other = replace(other, semantic_fingerprint=_semantic_fingerprint(other.signal_predicate, other.exit_predicate))
    frozen = DurableFrozenCandidateContractV1.from_semantic_record(other, hypothesis,
        factor_event_registry_identities=contract.factor_event_registry_identities,
        research_period_identity=contract.research_period_identity, policy_identity=contract.policy_identity,
        source_provenance=contract.source_provenance, created_frozen_timestamp=contract.created_frozen_timestamp)
    with pytest.raises((ValueError, RuntimeError), match="R1_|UNSUPPORTED"):
        prepare((caller, policy, frozen, other, cache))


def test_small_policy_mismatch_is_not_rewritten(case):
    inputs = prepare(case)
    with pytest.raises(ValueError, match="R1_POLICY_EXECUTION_MISMATCH"):
        case[0]._invoke_runner(replace(case[1], max_positions=1), case[3], "MISMATCH", inputs,
            portfolio_name="SMALL_CAPITAL_10K")


def test_fresh_process_formal_subpath(case, tmp_path):
    from chanlun_trader.research_factory.source_dependencies import SOURCE_ROOT
    contract_path = case[0].root / "caller-contract.json"
    contract_path.write_text(json.dumps(case[2].to_dict()), encoding="utf-8")
    before = snapshot(case[0].root)
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SOURCE_ROOT / "tests/isolation"), str(SOURCE_ROOT / "src")]))
    output = Path(os.environ.get("CHANLUN_PROCESS_EVIDENCE_DIR", str(tmp_path / "cold-evidence"))) / "r1-caller"
    output.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("r1_caller_cold_worker.py")),
            str(case[0].root), case[0].objective_id], cwd=tmp_path, env=environment, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        (output / "stdout.bin").write_bytes(exc.stdout or b"")
        (output / "stderr.bin").write_bytes(exc.stderr or b"")
        (output / "status.json").write_text(json.dumps({"status": "TIMEOUT", "input_root": str(case[0].root)}), encoding="utf-8")
        raise
    (output / "stdout.bin").write_bytes(result.stdout)
    (output / "stderr.bin").write_bytes(result.stderr)
    (output / "status.json").write_text(json.dumps({"returncode": result.returncode, "input_root": str(case[0].root)}), encoding="utf-8")
    assert result.returncode == 0, result.stdout.decode("utf-8") + result.stderr.decode("utf-8")
    assert json.loads(result.stdout)["synthetic_engine_run"] == 1
    assert snapshot(case[0].root) == before


def test_cache_available_at_preserved(tmp_path, monkeypatch):
    caller, policy, contract, record, cache = fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    inputs = caller._prepare_inputs(policy, record, caller._corrected_module(), cache, contract=contract)
    assert "available_at" in inputs["factor_values"]
    pd.testing.assert_series_equal(inputs["factor_values"].available_at, pd.read_parquet(cache).available_at)
