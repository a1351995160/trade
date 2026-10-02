"""真实公共链路：先核对完整登记范围，再冻结资料合格范围和完整排除依据。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_bundle
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
from chanlun_trader.research_factory.universe_status_v1 import universe_task_metadata_v1
from test_universe_submission_v1 import public_universe_case


TARGETS = ["000001.SZ", "300001.SZ", "600000.SH"]
QUALIFIED = ["000001.SZ", "600000.SH"]


def _register(service, root, accesses, *, dataset_id="sample"):
    provider = UniverseDataProviderV1({"data": root}, accesses.append)
    provider.register(dataset_id, "data", "manifest.json")
    service.provider = provider


def _source_changed(root, name):
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
    manifest["files"][name]["sha256"] = digest
    if name == "daily.parquet":
        manifest["files"][name]["evidence"]["source_sha256"] = digest
    path.write_text(json.dumps(manifest), encoding="utf-8")


def qualified_case(tmp_path, *, defect="state"):
    service, request, authority, accesses = public_universe_case(tmp_path)
    data = tmp_path / "data"
    if defect == "state":
        states = pd.read_parquet(data / "states.parquet")
        states.loc[states.symbol.eq("300001.SZ"), "st_status"] = "UNKNOWN"
        states.to_parquet(data / "states.parquet", index=False)
        _source_changed(data, "states.parquet")
    elif defect == "price":
        daily = pd.read_parquet(data / "daily.parquet")
        missing = daily.symbol.eq("300001.SZ") & daily.date.eq(request["account_start"])
        assert missing.sum() == 1
        daily.loc[~missing].to_parquet(data / "daily.parquet", index=False)
        _source_changed(data, "daily.parquet")
    else:
        raise AssertionError(defect)
    _register(service, data, accesses)
    request.update(version="FULL_UNIVERSE_SUBMISSION_V2", account_scope="DATA_QUALIFIED")
    return service, request, authority, accesses


def _scan(service, request):
    return service.scan(request, service.preview(request)["preview_identity"])


def _freeze(service, request):
    return service.freeze(request, service.preview(request)["preview_identity"])


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_correcting_position_limit_creates_new_check_without_changing_qualified_scope(tmp_path):
    service, request, authority, _ = qualified_case(tmp_path)
    request['max_positions'] = 3
    first = _scan(service, request)
    path = service.root / 'signal-scans' / first['scan_id'] / 'QUALIFICATION_SCOPE.json'
    previous = path.read_bytes()
    with pytest.raises(ValueError, match='SUBMISSION_QUALIFIED_POSITION_LIMIT_EXCEEDS_SCOPE'):
        _freeze(service, request)
    request['max_positions'] = 2
    corrected = _scan(service, request)
    assert corrected['scan_id'] != first['scan_id']
    assert corrected['qualification_scope'] == first['qualification_scope']
    assert path.read_bytes() == previous
    frozen = _freeze(service, request)
    assert frozen['qualification_scope']['qualified_symbols'] == QUALIFIED
    assert not Path(authority['budget_path']).exists()


def test_qualified_cache_binds_corporate_account_audit_source(tmp_path, monkeypatch):
    from chanlun_trader.research_factory import universe_scan_service_v1 as scan
    service, request, _, _ = qualified_case(tmp_path)
    result = _scan(service, request)
    original = scan._sha
    monkeypatch.setattr(scan, '_sha', lambda path: '0' * 64
        if path.name == 'universe_corporate_accounting_v2.py' else original(path))
    with pytest.raises(ValueError, match='UNIVERSE_SCAN_INTENT_CHANGED_REQUIRES_RECONCILIATION'):
        _scan(service, request)
    with pytest.raises(ValueError, match='UNIVERSE_SCAN_CODE_CHANGED'):
        scan.validated_scan_snapshot(service, result)


@pytest.mark.parametrize("defect", ["state", "price"])
def test_full_pool_checked_then_all_data_qualified_symbols_are_frozen(tmp_path, defect):
    service, request, authority, accesses = qualified_case(tmp_path, defect=defect)
    full_request = {key: value for key, value in request.items() if key != "account_scope"}
    full_request["version"] = "FULL_UNIVERSE_SUBMISSION_V1"
    with pytest.raises(ValueError, match="UNIVERSE_ACCOUNT_INPUT_NOT_READY"):
        _freeze(service, full_request)
    preview = service.preview(request)
    assert preview["request"]["symbols"] == TARGETS
    scanned = service.scan(request, preview["preview_identity"])
    assert scanned["status"] == "QUALIFIED_SCOPE_READY", scanned
    assert scanned["processed_target_count"] == 3
    assert scanned["strategy_signals_scanned"] is False
    assert scanned["signals_evaluated_target_count"] == 0
    assert scanned["account_executed"] is False
    assert scanned["account_budget_created"] is False
    assert not Path(authority["budget_path"]).exists()
    scope = scanned["qualification_scope"]
    assert scope["target_symbols"] == TARGETS
    assert scope["qualified_symbols"] == QUALIFIED
    assert [row["symbol"] for row in scope["excluded"]] == ["300001.SZ"]
    assert scope["excluded"][0]["reasons"]
    assert scope["account_audit"]["window"]["symbols"] == TARGETS
    assert scope["parent_input_identity"] == scope["account_audit"]["input_identity"]
    assert scope["scope_identity"] == stable_hash({k: v for k, v in scope.items() if k != "scope_identity"})
    root = service.root / "signal-scans" / scanned["scan_id"]
    assert _read(root / "QUALIFICATION_SCOPE.json") == scope
    exclusions = pd.read_csv(root / "EXCLUSIONS.csv", dtype=str)
    assert len(exclusions) == 1 and exclusions.iloc[0, 0] == "300001.SZ"
    assert (root / "REPORT_CN.md").is_file()
    frozen = service.freeze(request, preview["preview_identity"])
    assert frozen["qualification_scope"] == scope
    job = _read(frozen["job_path"])
    snapshots = set()
    for name, item in job["items"].items():
        assert item["loader"].endswith(":load_frozen_qualified_bundle")
        assert item["backend_options"]["window"]["symbols"] == QUALIFIED
        assert job["plans"][name]["backend"]["window"]["symbols"] == QUALIFIED
        parent_path = Path(item["loader_kwargs"]["parent_path"])
        assert parent_path == Path(frozen["job_path"]).parent.parent / "PARENT" / "INPUT.json"
        assert _read(parent_path)["window"]["symbols"] == TARGETS
        from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_qualified_bundle
        loaded = load_frozen_qualified_bundle(**item["loader_kwargs"])
        assert loaded["input_identity"] == frozen["input_identity"]
        assert sorted(loaded["frame"]["daily"].symbol.unique()) == QUALIFIED
        assert loaded["frame"]["qualified_scope"] == scope
        snapshots.add(tuple(sorted(item["loader_kwargs"].items())))
    assert len(snapshots) == 1  # 成本压力不能另选股票、改变数据资格范围。
    assert service.provider.catalog()["datasets"][0]["target_symbols"] == TARGETS
    assert accesses == [] and not Path(authority["budget_path"]).exists()


@pytest.mark.parametrize("field,value", [
    ("symbols", ["000001.SZ"]), ("symbols", TARGETS),
    ("account_scope", "FULL_REQUIRED"), ("account_scope", "BEST_RETURNS"),
])
def test_qualified_public_request_cannot_supply_manual_or_performance_scope(tmp_path, field, value):
    service, request, authority, accesses = qualified_case(tmp_path)
    request[field] = value
    with pytest.raises(ValueError, match="UNIVERSE_SUBMISSION_REQUEST_FIELDS_INVALID|UNIVERSE_QUALIFIED_POLICY_REQUIRED"):
        service.preview(request)
    assert accesses == [] and not service.root.exists()
    assert not Path(authority["budget_path"]).exists()


def test_default_provider_still_requires_full_registered_account_data(tmp_path):
    service, request, authority, _ = qualified_case(tmp_path)
    params = {key: request[key] for key in ("feature_start", "account_start", "account_end", "purpose")}
    with pytest.raises(ValueError, match="UNIVERSE_ACCOUNT_INPUT_NOT_READY"):
        service.provider.prepare(request["dataset_id"], **params, authorization=authority["data_authorization"])
    with pytest.raises(ValueError, match="DATA_FULL_UNIVERSE_REQUIRED"):
        service.provider.prepare(request["dataset_id"], symbols=QUALIFIED, **params,
                                 authorization=authority["data_authorization"])


def test_unattributable_common_problem_cannot_be_hidden_by_excluding_a_stock(tmp_path):
    service, request, authority, accesses = qualified_case(tmp_path)
    path = tmp_path / "data" / "manifest.json"
    manifest = _read(path)
    manifest["corporate_actions_complete"] = False
    path.write_text(json.dumps(manifest), encoding="utf-8")
    _register(service, path.parent, accesses)
    scanned = _scan(service, request)
    assert scanned["qualified_account_ready"] is False
    assert scanned["account_executed"] is False and scanned["strategy_qualified"] is False
    assert scanned["qualification_scope"]["blocking_global_gaps"]
    with pytest.raises(ValueError, match="UNIVERSE_ACCOUNT_INPUT_NOT_READY"):
        _freeze(service, request)
    assert not Path(authority["budget_path"]).exists()
    assert not list(service.root.glob("*/TASK.json"))


def test_empty_qualified_pool_keeps_all_exclusions_and_cannot_execute(tmp_path):
    service, request, authority, accesses = qualified_case(tmp_path)
    data = tmp_path / "data"
    states = pd.read_parquet(data / "states.parquet")
    states["st_status"] = "UNKNOWN"
    states.to_parquet(data / "states.parquet", index=False)
    _source_changed(data, "states.parquet")
    _register(service, data, accesses)
    scanned = _scan(service, request)
    assert scanned["status"] == "QUALIFIED_SCOPE_BLOCKED"
    scope = scanned["qualification_scope"]
    assert scope["target_symbols"] == TARGETS and scope["qualified_symbols"] == []
    assert [row["symbol"] for row in scope["excluded"]] == TARGETS
    assert "UNIVERSE_QUALIFIED_SCOPE_EMPTY" in scope["blocking_global_gaps"]
    with pytest.raises(ValueError, match="UNIVERSE_ACCOUNT_INPUT_NOT_READY"):
        _freeze(service, request)
    assert not Path(authority["budget_path"]).exists()


def test_data_scope_is_identical_when_same_fields_have_no_possible_buy_signal(tmp_path):
    service, request, authority, _ = qualified_case(tmp_path)
    first = _scan(service, request)
    assert first["status"] == "QUALIFIED_SCOPE_READY", first
    changed = deepcopy(request)
    changed["strategy_id"] = "NO_POSSIBLE_SIGNAL"
    changed["rule"]["buy"]["args"][1]["params"]["value"] = 1000000
    second = _scan(service, changed)
    assert second["status"] == "QUALIFIED_SCOPE_READY", second
    assert second["qualification_scope"] == first["qualification_scope"]
    assert second["strategy_signals_scanned"] is False
    assert second["signals_evaluated_target_count"] == 0
    assert not Path(authority["budget_path"]).exists()


def test_qualified_status_uses_metadata_without_market_reads_or_new_budget(tmp_path, monkeypatch):
    service, request, authority, accesses = qualified_case(tmp_path)
    frozen = _freeze(service, request)
    before = deepcopy(accesses)
    def forbidden(*args, **kwargs):
        pytest.fail("只读状态不应重新读取全池行情或准备输入")
    monkeypatch.setattr(service.provider, "prepare", forbidden)
    monkeypatch.setattr(pd, "read_parquet", forbidden)
    result = universe_task_metadata_v1(service, frozen["task_id"])
    assert result["registered_target_count"] == 3
    assert result["qualified_target_count"] == 2
    assert result["excluded_target_count"] == 1
    assert result["qualification_scope"] == frozen["qualification_scope"]
    assert result["metadata_only"] is True and result["freshly_reverified"] is False
    assert result["strategy_qualified"] is False
    assert accesses == before and not Path(authority["budget_path"]).exists()


def test_rehashed_forged_exclusion_is_rejected_by_recomputing_parent_qualification(tmp_path):
    service, request, _, _ = qualified_case(tmp_path)
    frozen = _freeze(service, request)
    job = _read(frozen["job_path"])
    kwargs = deepcopy(next(iter(job["items"].values()))["loader_kwargs"])
    source = Path(kwargs["path"])
    snapshot = _read(source)
    receipt = snapshot["bundle"]["qualified_scope"]
    receipt["qualified_symbols"] = ["000001.SZ"]
    receipt["excluded"].append({"symbol": "600000.SH", "reasons": ["FAKE_DATA_GAP"]})
    receipt["scope_identity"] = stable_hash({k: v for k, v in receipt.items() if k != "scope_identity"})
    # 同时重新计算输入和文件哈希，证明拒绝依据不是只比较自报哈希。
    loaded = load_frozen_bundle(path=kwargs["path"], sha256=kwargs["sha256"],
                                input_identity=kwargs["input_identity"])
    loaded["frame"]["qualified_scope"] = deepcopy(receipt)
    from chanlun_trader.research_factory.universe_account_inputs_v1 import universe_input_identity_v1
    snapshot["input_identity"] = universe_input_identity_v1(loaded["frame"], snapshot["window"])
    source.write_text(json.dumps(snapshot), encoding="utf-8")
    kwargs.update(sha256=hashlib.sha256(source.read_bytes()).hexdigest(), input_identity=snapshot["input_identity"])
    from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_qualified_bundle
    with pytest.raises(ValueError, match="UNIVERSE_QUALIFIED_DERIVATION_CONFLICT"):
        load_frozen_qualified_bundle(**kwargs)


def test_repairs_create_a_new_scope_without_overwriting_original_exclusions(tmp_path):
    service, request, authority, accesses = qualified_case(tmp_path)
    first = _scan(service, request)
    frozen = _freeze(service, request)
    old_job = _read(frozen["job_path"])
    old_kwargs = deepcopy(next(iter(old_job["items"].values()))["loader_kwargs"])
    archive = service.root / "signal-scans" / first["scan_id"] / "QUALIFICATION_SCOPE.json"
    original_bytes = archive.read_bytes()
    data = tmp_path / "data"
    states = pd.read_parquet(data / "states.parquet")
    states.loc[states.symbol.eq("300001.SZ"), "st_status"] = "NORMAL"
    states.to_parquet(data / "states.parquet", index=False)
    _source_changed(data, "states.parquet")
    _register(service, data, accesses, dataset_id="repaired")
    request["dataset_id"] = "repaired"
    authority["data_authorization"]["dataset_ids"].append("repaired")
    second = _scan(service, request)
    assert second["status"] == "QUALIFIED_SCOPE_READY"
    assert second["qualification_scope"]["qualified_symbols"] == TARGETS
    assert second["qualification_scope"]["excluded"] == []
    assert second["qualification_scope"]["scope_identity"] != first["qualification_scope"]["scope_identity"]
    assert second["qualification_scope"]["parent_input_identity"] != first["qualification_scope"]["parent_input_identity"]
    assert archive.read_bytes() == original_bytes
    from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_qualified_bundle
    restored_old = load_frozen_qualified_bundle(**old_kwargs)
    assert restored_old["input_identity"] == frozen["input_identity"]
    assert restored_old["frame"]["qualified_scope"] == first["qualification_scope"]
    assert sorted(restored_old["frame"]["daily"].symbol.unique()) == QUALIFIED
    assert not Path(authority["budget_path"]).exists()


def _assert_qualified_account_reports(task, job):
    root = Path(task["job_path"]).parent
    task_root = root.parent
    scope_path, exclusions_path = task_root / "QUALIFICATION_SCOPE.json", task_root / "EXCLUSIONS.csv"
    assert _read(scope_path) == task["qualification_scope"]
    exclusions = pd.read_csv(exclusions_path, dtype=str)
    assert len(exclusions) == 1 and exclusions.iloc[0, 0] == "300001.SZ"
    for name, item in job["items"].items():
        report = (root / (name + "_REPORT.md")).read_text(encoding="utf-8")
        assert "登记全池3只" in report and "合格执行2只" in report and "排除1只" in report
        assert "回顾确定" in report and "不证明历史可投资范围完整" in report
        assert scope_path.as_posix() in report and exclusions_path.as_posix() in report
        assert str(scope_path) in item["dependency_files"]
        assert str(exclusions_path) in item["dependency_files"]


def _approved_qualified_case(tmp_path):
    """恢复测试的公共服务配置；账户、治理和 worker 不作替换。"""
    service, request, authority, accesses = qualified_case(tmp_path)
    preview = service.preview(request)
    permit = {key: preview["request"][key] for key in (
        "initial_cash", "symbols", "feature_start", "account_start", "account_end",
        "max_positions", "max_symbol_exposure_bps", "costs", "benchmark")}
    authority["account_authorization"] = {**permit, "purpose": "FROZEN_PUBLIC_ACCOUNT_PLANS",
        "rule_identity": preview["rule_identity"], "max_account_jobs": 2}
    task = service.freeze(request, preview["preview_identity"])
    approval = service.approval_preview(task["task_id"])
    service.approve(task["task_id"], approval["preview_identity"])
    path = Path(task["job_path"])
    job = _read(path)
    return {"service": service, "request": request, "authority": authority, "accesses": accesses,
        "task": task, "path": path, "job": job, "name": list(job["plans"])[-1], "root": path.parent}


def test_actual_qualified_account_chain_requires_full_pool_authority_then_verifies_both_costs(tmp_path):
    from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
    from chanlun_trader.research_factory.research_evidence_v1 import verify_job_evidence
    from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_qualified_bundle
    from chanlun_trader.research_factory.universe_evidence_v1 import reconstruct_universe_account
    service, request, authority, _ = qualified_case(tmp_path)
    preview = service.preview(request)
    frozen = service.freeze(request, preview["preview_identity"])
    approval = service.approval_preview(frozen["task_id"])
    with pytest.raises(PermissionError, match="SUBMISSION_ACCOUNT_AUTHORIZATION_REQUIRED"):
        service.approve(frozen["task_id"], approval["preview_identity"])
    permit = {key: preview["request"][key] for key in (
        "initial_cash", "symbols", "feature_start", "account_start", "account_end",
        "max_positions", "max_symbol_exposure_bps", "costs", "benchmark")}
    authority["account_authorization"] = {**permit, "purpose": "FROZEN_PUBLIC_ACCOUNT_PLANS",
        "rule_identity": preview["rule_identity"], "max_account_jobs": 2}
    authority["account_authorization"]["symbols"] = QUALIFIED
    with pytest.raises(PermissionError, match="SUBMISSION_ACCOUNT_SCOPE_OUTSIDE_AUTHORITY"):
        service.approve(frozen["task_id"], approval["preview_identity"])
    assert not Path(authority["budget_path"]).exists()
    authority["account_authorization"]["symbols"] = TARGETS
    service.approve(frozen["task_id"], approval["preview_identity"])
    complete = service.start(frozen["task_id"])
    assert complete["status"] == "ACCOUNT_VERIFIED", complete
    assert complete["strategy_qualified"] is False
    assert complete["qualification_scope"] == frozen["qualification_scope"]
    assert complete["registered_target_count"] == 3
    assert complete["qualified_target_count"] == 2 and complete["excluded_target_count"] == 1
    root = Path(frozen["job_path"]).parent
    assert _read(root / "CONFIRMATION.json")["source"]["qualified_scope_identity"] == frozen["qualification_scope"]["scope_identity"]
    job = _read(frozen["job_path"])
    _assert_qualified_account_reports(frozen, job)
    budget = SearchBudgetRegistryV1(job["objective_id"], job["budget_path"]).snapshot()
    assert len(budget["settled_reservations"]) == 2
    assert set(budget["settled_reservations"].values()) == {"CONSUMED"}
    for name, item in job["items"].items():
        result = _read(root / (name + "_RESULT.json"))
        assert result["fills"]
        assert {fill["symbol"] for fill in result["fills"]} <= set(QUALIFIED)
        assert "300001.SZ" not in {fill["symbol"] for fill in result["fills"]}
        verification = verify_job_evidence(frozen["job_path"], name=name)
        assert verification["status"] == "PASS", verification
        loaded = load_frozen_qualified_bundle(**item["loader_kwargs"])
        plan = job["plans"][name]
        audit = reconstruct_universe_account(loaded["frame"], plan["backend"]["window"], result,
            initial_cash=plan["backend"]["initial_cash"], costs=plan["backend"]["costs"],
            strategy_id=name, rule=request["rule"])
        assert audit["daily_accounts"] == result["daily_accounts"]
        assert audit["metrics"] == result["metrics"]
        assert audit["strategy_qualified"] is False


def test_qualified_public_resume_matches_continuous_result_without_new_budget(tmp_path, monkeypatch):
    from chanlun_trader.research_factory.research_evidence_v1 import verify_job_evidence
    from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_qualified_bundle
    import test_universe_public_recovery_v1 as recovery
    continuous = _approved_qualified_case(tmp_path / "continuous")
    completed = continuous["service"].start(continuous["task"]["task_id"])
    assert completed["status"] == "ACCOUNT_VERIFIED", completed
    # 只替换既有测试 helper 的临时输入配置，复用其真实原子提交后退出进程的故障注入。
    # 原账户、checkpoint、Windows Job、公开 resume 与预算实现全部保持真实。
    with monkeypatch.context() as fixture_setup:
        fixture_setup.setattr(recovery, "approved_case", _approved_qualified_case)
        case = recovery.interrupted_case(tmp_path / "interrupted")
    assert case["resource"]["returncode"] == 73
    assert case["checkpoint"]["last_day"] > case["request"]["account_start"]
    resumed = case["service"].resume(case["task"]["task_id"])
    assert resumed["status"] == "ACCOUNT_VERIFIED", resumed
    assert resumed["registered_target_count"] == 3
    assert resumed["qualified_target_count"] == 2 and resumed["excluded_target_count"] == 1
    assert resumed["qualification_scope"] == case["task"]["qualification_scope"]
    assert resumed["strategy_qualified"] is False
    recovery.assert_original_consumption(case)
    resource = _read(case["root"] / (case["name"] + "_RESOURCE.json"))
    assert resource["resumed"] is True and resource["original_budget_reused"] is True
    assert resource["returncode"] == 0 and resource["timed_out"] is False
    _assert_qualified_account_reports(case["task"], case["job"])
    for name, item in case["job"]["items"].items():
        result = _read(case["root"] / (name + "_RESULT.json"))
        reference = _read(continuous["root"] / (name + "_RESULT.json"))
        assert result["fills"]
        for key in ("fills", "daily_accounts", "metrics", "scan_days", "final_account_checkpoint"):
            assert result[key] == reference[key], key
        assert {fill["symbol"] for fill in result["fills"]} <= set(QUALIFIED)
        verification = verify_job_evidence(case["path"], name=name)
        assert verification["status"] == "PASS", verification
        assert verification["evidence_layers"]["formal_qualification"] is False
        restored = load_frozen_qualified_bundle(**item["loader_kwargs"])
        assert restored["frame"]["qualified_scope"] == case["task"]["qualification_scope"]
        assert item["backend_options"]["window"]["symbols"] == QUALIFIED
    recovery.assert_original_consumption(case)
