"""公共全范围提交：范围、数据许可、冻结身份与只读状态不能互相替代。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research_factory.board_execution_policy_v1 import board_policy_identity
from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1, load_frozen_bundle
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
from chanlun_trader.research_factory.universe_status_v1 import universe_task_metadata_v1
from test_strategy_submission_v1 import connected, node
from test_tdx_research_adapter_v1 import evidence
from universe_test_fixture_v1 import fixture


def public_universe_case(tmp_path, *, complete=True):
    """真实提供器 + 临时合成原件，不接触磁盘上的研究行情或正式授权。"""
    data = tmp_path / "data"
    data.mkdir(parents=True)
    window, bundle = fixture()
    daily = bundle["daily"]
    states = bundle["states"].copy()
    states["source"] = "states"
    states["listing_date_source"] = "states"
    daily.to_parquet(data / "daily.parquet", index=False)
    states.to_parquet(data / "states.parquet", index=False)
    (data / "calendar.json").write_text(json.dumps(window["calendar"]), encoding="utf-8")
    coverage = [{"symbols": window["symbols"], "start": window["feature_start"],
                 "end": window["account_end"], "source": "actions", "complete": complete,
                 "event_types": ["CASH_DIVIDEND"]}]
    (data / "actions.json").write_text(json.dumps(coverage), encoding="utf-8")
    def digest(name):
        return hashlib.sha256((data / name).read_bytes()).hexdigest()
    proof = evidence(digest("daily.parquet"))
    proof.update(prev_close_semantics="VENDOR_REFERENCE",
                 reference_evidence={"source": "SYNTHETIC_REFERENCE", "sha256": "f" * 64})
    files = {}
    for name, kind, form, source in [("daily.parquet", "DAILY", "PARQUET", "daily"),
        ("states.parquet", "STATES", "PARQUET", "states"),
        ("calendar.json", "CALENDAR", "JSON", "calendar"),
        ("actions.json", "CORPORATE_ACTION_COVERAGE", "JSON", "actions")]:
        files[name] = {"kind": kind, "format": form, "source_id": source,
                       "sha256": digest(name), "start": window["feature_start"], "end": window["account_end"]}
    files["daily.parquet"].update(symbols=window["symbols"], evidence=proof)
    proof["source_id"] = "daily"
    files["states.parquet"]["date_columns"] = ["effective_date", "valid_to"]
    boards = {symbol: "CHINEXT" if symbol.startswith("30") else
              "SH_MAIN" if symbol.endswith("SH") else "SZ_MAIN" for symbol in window["symbols"]}
    manifest = {"adapter": "TDX_FULL_UNIVERSE_V1", "universe_id": "THREE_BOARDS",
        "start": window["feature_start"], "end": window["account_end"], "files": files,
        "corporate_actions_complete": complete, "board_policy_identity": board_policy_identity(),
        "master": {"source": "SYNTHETIC_HISTORICAL_MASTER", "records": [
            {"symbol": symbol, "board": boards[symbol], "listing_date": 20000103}
            for symbol in window["symbols"]]}}
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    accesses = []
    provider = UniverseDataProviderV1({"data": data}, accesses.append)
    provider.register("sample", "data", "manifest.json")
    authority = {"objective_id": "PUBLIC_UNIVERSE_TEST", "budget_path": str(tmp_path / "budget.json"),
        "data_authorization": {"authorization_id": "SYNTHETIC_DATA_AUTH", "purpose": "EXPLORATORY",
            "dataset_ids": ["sample"], "start": window["feature_start"], "end": window["account_end"]},
        "source": {"origin": "USER_EXPLICIT_CURRENT_TASK", "statement": "三板块合成工程测试"},
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    service = StrategySubmissionV1(provider, lambda ref: deepcopy(authority), tmp_path / "tasks", connected)
    rule = {"version": "RESEARCH_RULE_STRATEGY_V3", "hypothesis": "合成固定规则", "change_reason": "验证公共入口",
        "buy": node("gt", node("field", "close"), node("const", value=11)),
        "sell": node("lt", node("field", "close"), node("const", value=5)),
        "market_filter": None, "min_hold_sessions": 0, "max_hold_sessions": 5,
        "cooldown_sessions": 0, "target_weight": .5, "indicator_instances": [],
        "exits": {"execution_mode": "CLOSE_CONFIRM_NEXT_SESSION_OPEN", "stop_loss_pct": None,
                  "take_profit_pct": None, "trailing_activate_pct": None, "trailing_pct": None}}
    request = {"version": "FULL_UNIVERSE_SUBMISSION_V1", "strategy_id": "FULL_FIXTURE",
        "rule": rule, "dataset_id": "sample", "universe_id": "THREE_BOARDS",
        "feature_start": window["feature_start"], "account_start": window["account_start"],
        "account_end": window["account_end"], "initial_cash": 50000, "max_positions": 2,
        "max_symbol_exposure_bps": 5000, "costs": ["BASE", "STRESS"],
        "benchmark": "CASH_AND_PRICE_REFERENCE", "purpose": "EXPLORATORY", "authorization_ref": "test"}
    return service, request, authority, accesses


def test_full_preview_uses_all_three_boards_without_authority_or_content_access(tmp_path):
    service, request, _, accesses = public_universe_case(tmp_path)
    service.authority = lambda ref: pytest.fail("预览不可访问授权")
    preview = service.preview(request)
    assert preview["request"]["symbols"] == ["000001.SZ", "300001.SZ", "600000.SH"]
    assert preview["coverage"]["target_count"] == 3
    assert {key: row["target_count"] for key, row in preview["coverage"]["by_board"].items()} == {
        "SZ_MAIN": 1, "SH_MAIN": 1, "CHINEXT": 1}
    assert preview["request"]["initial_cash"] == 50000
    assert "symbols" not in request and accesses == []
    assert not service.root.exists()


@pytest.mark.parametrize("symbols", [["000001.SZ"], ["000001.SZ", "300001.SZ", "600000.SH"]])
def test_full_request_cannot_inject_even_a_matching_symbols_field(tmp_path, symbols):
    service, request, _, accesses = public_universe_case(tmp_path)
    request["symbols"] = symbols
    with pytest.raises(ValueError, match="UNIVERSE_SUBMISSION_REQUEST_FIELDS_INVALID"):
        service.preview(request)
    assert accesses == []


def test_diagnosis_keeps_entire_denominator_and_explicit_gaps_without_account_budget(tmp_path):
    service, request, authority, accesses = public_universe_case(tmp_path, complete=False)
    preview = service.preview(request)
    first = service.diagnose(request, preview["preview_identity"])
    assert first["status"] == "DATA_GAPS"
    assert first["coverage"]["target_symbol_count"] == 3
    assert first["coverage"]["gaps"]
    assert first["historical_availability"] == "MODELED"
    assert first["account_executed"] is False and first["account_budget_created"] is False
    assert first["strategy_signals_scanned"] is False and first["strategy_qualified"] is False
    assert not Path(authority["budget_path"]).exists()
    assert not list(service.root.glob("*/TASK.json"))
    before = deepcopy(accesses)
    second = service.diagnose(request, preview["preview_identity"])
    assert second["diagnosis_identity"] == first["diagnosis_identity"]
    assert second["recorded_only"] is True and second["content_reread"] is False
    assert accesses == before


def test_diagnosis_ready_does_not_grant_account_execution_or_strategy_qualification(tmp_path):
    service, request, authority, _ = public_universe_case(tmp_path)
    result = service.diagnose(request, service.preview(request)["preview_identity"])
    assert result["status"] == "ACCOUNT_INPUTS_READY"
    assert not result["account_executed"] and not result["account_budget_created"]
    assert not result["strategy_qualified"] and not Path(authority["budget_path"]).exists()
    assert not list(service.root.glob("*/TASK.json"))


@pytest.mark.parametrize("change", ["expiry", "purpose", "dataset", "dataset_type", "dates", "source"])
def test_diagnosis_authority_and_scope_validated_before_cached_or_new_content(tmp_path, change):
    service, request, authority, accesses = public_universe_case(tmp_path)
    preview = service.preview(request)
    if change == "expiry":
        authority["expires_at"] = "2000-01-01T00:00:00+00:00"
    elif change == "purpose":
        authority["data_authorization"]["purpose"] = "QUALIFICATION"
    elif change == "dataset":
        authority["data_authorization"]["dataset_ids"] = ["other"]
    elif change == "dataset_type":
        authority["data_authorization"]["dataset_ids"] = "sample"
    elif change == "dates":
        authority["data_authorization"]["end"] = request["account_start"]
    else:
        authority["source"]["origin"] = "AI_INFERRED"
    with pytest.raises(PermissionError, match="UNIVERSE_DIAGNOSIS"):
        service.diagnose(request, preview["preview_identity"])
    assert accesses == [] and not service.root.exists()


def test_stale_preview_refused_before_reading_registered_sources(tmp_path):
    service, request, _, accesses = public_universe_case(tmp_path)
    identity = service.preview(request)["preview_identity"]
    request["initial_cash"] = 100000
    with pytest.raises(ValueError, match="SUBMISSION_PREVIEW_CHANGED"):
        service.diagnose(request, identity)
    assert accesses == []


def test_full_freeze_roundtrip_uses_new_backend_global_funds_and_price_reference(tmp_path, monkeypatch):
    service, request, authority, _ = public_universe_case(tmp_path)
    monkeypatch.setattr(service.provider, 'prepare', lambda *args, **kwargs: pytest.fail('宿主进程不能准备全市场行情'))
    original = Path.read_bytes
    def no_full_frame_bytes(self):
        if self.suffix == '.parquet':
            pytest.fail('宿主进程冻结不能将整份行情或状态文件载入bytes')
        return original(self)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, 'read_bytes', no_full_frame_bytes)
        frozen = service.freeze(request, service.preview(request)["preview_identity"])
    job = json.loads(Path(frozen["job_path"]).read_text(encoding="utf-8"))
    assert len(job["items"]) == 2 and job["benchmark_id"] is None
    assert job["benchmark_mode"] == "CASH_AND_PRICE_REFERENCE"
    assert job["input_identity"] == frozen["input_identity"]
    for name, item in job["items"].items():
        assert item["benchmark_mode"] == job["benchmark_mode"]
        assert job["plans"][name]["runtime"]["benchmark_mode"] == job["benchmark_mode"]
        assert item["backend_options"]["backend_version"] == "UNIVERSE_ACCOUNT_BACKEND_V1"
        assert item["backend_options"]["initial_cash"] == 50000
        assert item["backend_options"]["window"]["symbols"] == ["000001.SZ", "300001.SZ", "600000.SH"]
        assert item["backend_options"]["checkpoint_path"].endswith(name + "_CHECKPOINT.json")
        loaded = load_frozen_bundle(**item["loader_kwargs"])
        assert loaded["input_identity"] == frozen["input_identity"]
        assert loaded["frame"]["listing_dates"]["300001.SZ"] == 20000103
    assert not Path(authority["budget_path"]).exists()


def test_task_coverage_is_metadata_only_without_market_reprepare(tmp_path, monkeypatch):
    service, request, authority, accesses = public_universe_case(tmp_path)
    frozen = service.freeze(request, service.preview(request)["preview_identity"])
    before = deepcopy(accesses)
    monkeypatch.setattr(service.provider, "prepare", lambda *args, **kwargs: pytest.fail("状态查询不能 prepare"))
    monkeypatch.setattr(pd, "read_parquet", lambda *args, **kwargs: pytest.fail("状态查询不能读取报价"))
    result = universe_task_metadata_v1(service, frozen["task_id"])
    assert result["metadata_only"] is True and result["freshly_reverified"] is False
    assert result["coverage"]["target_symbol_count"] == 3
    assert result["coverage"]["by_board"]["CHINEXT"]["target_count"] == 1
    assert result["strategy_qualified"] is False and result["historical_availability"] == "MODELED"
    assert accesses == before and not Path(authority["budget_path"]).exists()


def test_metadata_status_checks_bound_input_hash_without_opening_frames(tmp_path, monkeypatch):
    service, request, _, _ = public_universe_case(tmp_path)
    frozen = service.freeze(request, service.preview(request)["preview_identity"])
    path = Path(frozen["job_path"]).parent.parent / "INPUT.json"
    with path.open("ab") as stream:
        stream.write(b" ")
    monkeypatch.setattr(pd, "read_parquet", lambda *args, **kwargs: pytest.fail("不应读取冻结行情"))
    with pytest.raises(ValueError, match="UNIVERSE_STATUS_INPUT_CHANGED"):
        universe_task_metadata_v1(service, frozen["task_id"])


def test_data_gap_cannot_freeze_then_restart_as_new_free_trial(tmp_path):
    service, request, authority, accesses = public_universe_case(tmp_path, complete=False)
    preview = service.preview(request)
    with pytest.raises(ValueError, match="UNIVERSE_ACCOUNT_INPUT_NOT_READY"):
        service.freeze(request, preview["preview_identity"])
    count = len(accesses)
    with pytest.raises(ValueError, match="ALREADY_FROZEN_OR_INCOMPLETE"):
        service.freeze(request, preview["preview_identity"])
    assert len(accesses) == count
    assert len(list(service.root.glob("*/FREEZE_FAILURE.json"))) == 1
    assert not list(service.root.glob("*/TASK.json")) and not Path(authority["budget_path"]).exists()


def test_universe_benchmark_mode_is_bound_to_frozen_plan(tmp_path):
    from scripts.run_strategy_account_v1 import validate_sources
    service, request, _, _ = public_universe_case(tmp_path)
    frozen = service.freeze(request, service.preview(request)["preview_identity"])
    job = json.loads(Path(frozen["job_path"]).read_text(encoding="utf-8"))
    job["benchmark_mode"] = "NONE"
    with pytest.raises(PermissionError, match="JOB_UNIVERSE_BENCHMARK_CONFLICT"):
        validate_sources(job)


@pytest.mark.parametrize('tamper', ['job_mode', 'task_plan'])
def test_start_checks_task_identity_before_any_budget_or_worker(tmp_path, monkeypatch, tamper):
    import scripts.run_strategy_account_v1 as runner
    service, request, authority, _ = public_universe_case(tmp_path)
    frozen = service.freeze(request, service.preview(request)["preview_identity"])
    path = Path(frozen["job_path"])
    if tamper == 'job_mode':
        value = json.loads(path.read_text(encoding='utf-8'))
        value['benchmark_mode'] = 'NONE'
    else:
        path = path.parent.parent / 'TASK.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        value['plan_ids'][next(iter(value['plan_ids']))] = 'f' * 64
    path.write_text(json.dumps(value), encoding='utf-8')
    monkeypatch.setattr(runner, 'execute_accounts', lambda *args, **kwargs: pytest.fail('身份冲突不能先执行账户'))
    with pytest.raises(ValueError, match='UNIVERSE_STATUS_JOB_CHANGED|SUBMISSION_FROZEN_PLAN_CHANGED'):
        service.start(frozen['task_id'])
    assert not Path(authority['budget_path']).exists()
    assert not list(Path(frozen['job_path']).parent.glob('*_START.json'))
