import hashlib
import json

import pandas as pd
import pytest

from r1_fixture import dataset, write_json
from chanlun_trader.research_factory.data_readiness import inspect_dataset
from chanlun_trader.research_factory.source_dependencies import SOURCE_ROOT


def inspect(root, **kwargs):
    return inspect_dataset(SOURCE_ROOT, root, start=20240703, end=20240704, **kwargs)


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def package(tmp_path):
    dataset(tmp_path)
    return tmp_path


def test_legal_scoped_positive_is_read_only_and_not_trial_ready(package):
    before = snapshot(package)
    result = inspect(package)
    assert result["status"] == "SYNTHETIC_SCOPE_READY", result
    assert result["coverage"]["expected_daily_rows"] == 8
    assert result["requirements"]["warmup_start"] == 20240701
    assert result["real_candidate_data_readiness"] == "NOT_VERIFIED"
    assert result["ready_for_real_trial"] is False
    assert len(result["reads"]) == 3
    assert snapshot(package) == before
    print("R1_REQUIREMENTS_EVIDENCE=" + json.dumps(result, ensure_ascii=False))


@pytest.mark.parametrize("mutation,reason", [
    ("missing_file", "FileNotFoundError"), ("missing_column", "amount"),
    ("missing_symbol", "DAILY_COVERAGE"), ("missing_warmup", "DAILY_COVERAGE"),
    ("duplicate", "DUPLICATE"), ("future", "AVAILABLE_AT"),
    ("units", "UNIT"), ("unknown_state", "UNKNOWN_SECURITY"),
    ("missing_state", "PIT_STATE_COVERAGE"), ("corporate_unknown", "CORPORATE_ACTION"),
    ("factor_missing", "VOLUME_ACCEL"), ("factor_future", "AVAILABLE_AT"),
    ("warmup_calendar", "WARMUP"), ("path_escape", "REFERENCE_PATH_ESCAPE"),
    ("contract_tamper", "content hash"), ("policy_tamper", "policy hash"),
    ("sealed_partition", "PARTITION_OUTSIDE_APPROVED_WINDOW"),
])
def test_missing_conflict_and_false_manifest(package, mutation, reason):
    path = package / "daily.parquet"
    frame = pd.read_parquet(path)
    if mutation == "missing_file":
        path.unlink()
    elif mutation == "missing_column":
        frame.drop(columns="amount").to_parquet(path)
    elif mutation == "missing_symbol":
        frame[frame.symbol != "600000.SH"].to_parquet(path)
    elif mutation == "missing_warmup":
        frame[frame.date != 20240701].to_parquet(path)
    elif mutation == "duplicate":
        pd.concat([frame, frame.iloc[:1]]).to_parquet(path)
    elif mutation in {"future", "units", "sealed_partition"}:
        column, value = {"future": ("available_at", "2024-07-05T15:00:00+08:00"), "units": ("volume_unit", "LOT"), "sealed_partition": ("date", 20250801)}[mutation]
        frame.loc[0, column] = value
        frame.to_parquet(path)
    elif mutation in {"unknown_state", "missing_state", "corporate_unknown"}:
        path = package / "state.parquet"
        frame = pd.read_parquet(path)
        if mutation == "missing_state":
            frame = frame.iloc[1:]
        elif mutation == "unknown_state":
            frame["suspended"] = frame["suspended"].astype(object)
            frame.loc[0, "suspended"] = None
        else:
            frame.loc[0, "corporate_action"] = "UNKNOWN"
        frame.to_parquet(path)
    elif mutation.startswith("factor_"):
        path = package / "values.parquet"
        frame = pd.read_parquet(path)
        if mutation == "factor_missing":
            frame = frame.drop(columns="VOLUME_ACCEL")
        else:
            frame.loc[0, "available_at"] = "2024-07-05T15:00:00+08:00"
        frame.to_parquet(path)
    elif mutation in {"warmup_calendar", "path_escape"}:
        path = package / "readiness.json"
        value = json.loads(path.read_bytes())
        value["calendar_sessions"] = [20240703, 20240704] if mutation == "warmup_calendar" else value["calendar_sessions"]
        if mutation == "path_escape":
            value["daily"] = "../outside.parquet"
        write_json(path, value)
    else:
        path = package / ("contract.json" if mutation == "contract_tamper" else "data/research/strategy_validation/validation_decision_policy_v2.json")
        value = json.loads(path.read_bytes())
        value["content_hash" if mutation == "contract_tamper" else "policy_hash"] = "wrong"
        write_json(path, value)
    before = snapshot(package)
    result = inspect(package)
    assert result["status"] != "SYNTHETIC_SCOPE_READY", result
    assert reason in json.dumps(result), result
    assert snapshot(package) == before


def test_identity_changes_invalidate_previous_report(package):
    old = inspect(package)
    assert old["status"] == "SYNTHETIC_SCOPE_READY", old
    bundle = json.loads((package / "readiness.json").read_bytes())
    bundle["dataset_version"] = "2"
    write_json(package / "readiness.json", bundle)
    new = inspect(package, previous_identity=old["input_identity"])
    assert new["status"] == "SYNTHETIC_SCOPE_READY"
    assert new["previous_identity_matches"] is False


def test_final_test_and_real_root_rejected_before_reads(tmp_path):
    result = inspect_dataset(SOURCE_ROOT, tmp_path / "absent", start=20250801, end=20250802)
    assert "FinalTestAccessViolation" in str(result)
    assert not result["reads"]
    result = inspect(SOURCE_ROOT)
    assert "SYNTHETIC_TEMP_ROOT_REQUIRED" in str(result)
    assert not result["reads"]


def test_two_packages_keep_separate_identities(tmp_path):
    left, right = tmp_path / "a", tmp_path / "b"
    left.mkdir()
    right.mkdir()
    dataset(left)
    dataset(right)
    first = inspect(left)
    bundle = json.loads((right / "readiness.json").read_bytes())
    bundle["dataset_id"] = "SECOND"
    write_json(right / "readiness.json", bundle)
    assert first["input_identity"] != inspect(right)["input_identity"]
    assert first == inspect(left)


def test_missing_definition_is_not_invented(package):
    from chanlun_trader.research_factory.common import stable_hash
    path = package / "factors.json"
    value = json.loads(path.read_bytes())
    value["factors"] = []
    write_json(path, value)
    bundle = json.loads((package / "readiness.json").read_bytes())
    bundle["registry_hash"] = stable_hash(value)
    write_json(package / "readiness.json", bundle)
    result = inspect(package)
    assert result["status"] != "SYNTHETIC_SCOPE_READY"
    assert result["requirements"]["warmup_bars"] is None
    assert "FACTOR_DEFINITION:VOLUME_ACCEL" in result["missing"]


def test_read_time_mutation_is_conflict(package, monkeypatch):
    from chanlun_trader.research.io_safety import GuardedResearchReader
    original = GuardedResearchReader.read_parquet

    def mutate_after_actual_read(self, path, *args, **kwargs):
        result = original(self, path, *args, **kwargs)
        if str(path).endswith("daily.parquet"):
            frame = pd.read_parquet(path)
            frame.loc[0, "amount"] += 1
            frame.to_parquet(path)
        return result

    monkeypatch.setattr(GuardedResearchReader, "read_parquet", mutate_after_actual_read)
    result = inspect(package)
    assert "INPUT_CHANGED_DURING_INSPECTION" in str(result)
    assert result["status"] != "SYNTHETIC_SCOPE_READY"


def test_known_suspension_changes_denominator_but_missing_price_does_not(package):
    state = pd.read_parquet(package / "state.parquet")
    # 只在末日停牌，避免预热连续性与复牌定义超出本例范围。
    state.loc[(state.date == 20240704) & (state.symbol == "600000.SH"), "suspended"] = True
    state.to_parquet(package / "state.parquet")
    for name in ("daily.parquet", "values.parquet"):
        frame = pd.read_parquet(package / name)
        frame[~((frame.date == 20240704) & (frame.symbol == "600000.SH"))].to_parquet(package / name)
    result = inspect(package)
    assert result["status"] == "SYNTHETIC_SCOPE_READY", result
    assert result["coverage"]["expected_daily_rows"] == 7


def test_root_link_rejected_before_dataset_read(package, tmp_path_factory):
    import os
    parent = tmp_path_factory.mktemp("r1-link")
    link = parent / "linked"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(package), str(link))
    else:
        link.symlink_to(package, target_is_directory=True)
    result = inspect(link)
    assert "LINKED_RESEARCH_ROOT" in str(result)
    assert not result["reads"]


def test_formal_minute_reader_and_validator_positive_and_bad_labels(tmp_path):
    from chanlun_trader.data.minute.base import BarTimestampSemantics
    from chanlun_trader.data.minute.validator import validate_5m, expected_session_times, DataQualityError
    from chanlun_trader.research.io_safety import GuardedResearchReader
    times = sorted(expected_session_times(BarTimestampSemantics.BAR_END))
    frame = pd.DataFrame([dict(symbol="000001.SZ", timestamp=f"2024-07-03 {time}:00+08:00", trade_date=20240703, bar_time=time, open=10., high=11., low=9., close=10., volume=100., amount=1000., source="SYNTHETIC") for time in times])
    path = tmp_path / "minute.parquet"
    frame.to_parquet(path)
    audit = []
    before = snapshot(tmp_path)
    actual = GuardedResearchReader(audit_sink=audit.append).read_5m(path, 20240703, 20240703)
    assert validate_5m(actual, BarTimestampSemantics.BAR_END).ok
    assert len(actual) == len(times) == 48
    assert snapshot(tmp_path) == before
    actual.loc[0, "timestamp"] = "2024-07-03 09:31:00+08:00"
    with pytest.raises(DataQualityError):
        validate_5m(actual, BarTimestampSemantics.BAR_END)


def test_inspect_process_forbids_writes(package):
    import os
    import subprocess
    import sys
    script = r'''
import json, sys
from chanlun_trader.research_factory.data_readiness import inspect_dataset
def deny_write(event, args):
    if event in {"os.mkdir", "os.remove", "os.rename", "os.rmdir"}:
        raise AssertionError("INSPECT_WRITE")
    if event == "open" and ((isinstance(args[1], str) and any(c in args[1] for c in "wax+")) or (isinstance(args[2], int) and args[2] & 3)):
        raise AssertionError("INSPECT_WRITE")
sys.addaudithook(deny_write)
print(json.dumps(inspect_dataset(sys.argv[1], sys.argv[2], start=20240703, end=20240704)))
'''
    result = subprocess.run([sys.executable, "-c", script, str(SOURCE_ROOT), str(package)], cwd=package, env=dict(os.environ, PYTHONPATH=os.pathsep.join([str(SOURCE_ROOT / "tests/isolation"), str(SOURCE_ROOT / "src")])), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "SYNTHETIC_SCOPE_READY"
