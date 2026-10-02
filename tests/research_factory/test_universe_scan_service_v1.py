"""公共信号扫描：真实资源边界与合成数据反例，不作为真实市场有效性证据。"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from chanlun_trader.research_factory import universe_scan_service_v1 as scan
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3, rule_capabilities
from chanlun_trader.research_factory.universe_account_inputs_v1 import UniverseAccountInputsV1
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1
from test_chinext_entry_parity_v1 import indicator_proposal
from test_universe_submission_v1 import public_universe_case
from universe_test_fixture_v1 import fixture, proposal
from test_universe_signal_scan_v1 import halted_ex_date_case


def call_scan(service, request):
    return scan.scan_universe(service, request, service.preview(request)["preview_identity"])


def synthetic_launcher(monkeypatch):
    """仅测试数据/持久化边界；资源测试另用真正 worker，不能混称。"""
    launches = []
    def launch(command, **kwargs):
        launches.append({"command": command, **kwargs})
        root = Path(command[-1])
        assert (root / "SCAN_INTENT.json").exists() and not (root / "INPUT.json").exists()
        kwargs["on_started"](12345)
        monkeypatch.setattr(scan, "HANDSHAKE", {"execution": kwargs["execution"],
            "memory_mib": kwargs["memory_mib"], "wall_seconds": kwargs["wall_seconds"]})
        code = scan._worker(root)
        return {"returncode": code, "timed_out": False, "stdout": b"", "stderr": b"",
                "resource_platform": "SYNTHETIC_NOT_OS_LIMIT_EVIDENCE"}
    monkeypatch.setattr(scan, "run_bounded_worker", launch)
    return launches


def replace_registered_source(service, name, change):
    root, metadata, _, _ = service.provider._datasets["sample"]
    from pandas import read_parquet
    frame = change(read_parquet(root / name))
    frame.to_parquet(root / name, index=False)
    metadata = deepcopy(metadata)
    metadata["files"][name]["sha256"] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if name == "daily.parquet":
        metadata["files"][name]["evidence"]["source_sha256"] = metadata["files"][name]["sha256"]
    (root / "updated_manifest.json").write_text(json.dumps(metadata), encoding="utf-8")
    accesses = []
    provider = UniverseDataProviderV1({"data": root}, accesses.append)
    provider.register("sample", "data", "updated_manifest.json")
    service.provider = provider
    return accesses


@pytest.mark.parametrize("metadata_format", ["original", "sorted_bom", "external"])
def test_true_public_scan_prepares_inside_bounded_worker_and_reuses_without_new_account_or_scan(tmp_path, monkeypatch, metadata_format):
    service, request, authority, accesses = public_universe_case(tmp_path)
    metadata_path = service.provider._metadata_paths["sample"]
    if metadata_format == "sorted_bom":
        manifest = service.provider._datasets["sample"][1]
        metadata_path.write_text(json.dumps(manifest, sort_keys=True, indent=1), encoding="utf-8-sig")
        provider = UniverseDataProviderV1(service.provider.roots, accesses.append)
        provider.register_manifest("sample", "data", metadata_path)
        service.provider = provider
        assert metadata_path.read_bytes().startswith(b"\xef\xbb\xbf")
    elif metadata_format == "external":
        external = tmp_path / "external-registration" / "original.json"
        external.parent.mkdir()
        external.write_bytes(metadata_path.read_bytes())
        provider = UniverseDataProviderV1(service.provider.roots, accesses.append)
        provider.register_manifest("sample", "data", external)
        service.provider = provider
        metadata_path = external.absolute()
        assert not metadata_path.is_relative_to(provider.roots["data"])
    normalized = service.preview(request)["request"]
    expected = service.provider.prepare(normalized["dataset_id"], universe_id=normalized["universe_id"],
        symbols=normalized["symbols"], feature_start=normalized["feature_start"],
        account_start=normalized["account_start"], account_end=normalized["account_end"],
        required_fields=["close"], authorization=authority["data_authorization"], stage="SCAN")
    accesses.clear()
    monkeypatch.setattr(service.provider, "prepare", lambda *a, **k: pytest.fail("主进程不加载行情"))
    result = call_scan(service, request)
    assert result["status"] == "CONDITIONS_EVALUATED", result
    assert result["input_identity"] == expected["input_identity"]
    assert result["processed_target_count"] == result["signals_evaluated_target_count"] == 3
    assert result["signals_evaluated_session_count"] == 60
    assert result["unknown_target_count"] == 0 and result["historical_availability"] == "MODELED"
    assert sorted(row["symbol"] for row in result["per_symbol"]) == ["000001.SZ", "300001.SZ", "600000.SH"]
    assert all(row["condition_counts"]["buy"] > 0 for row in result["per_symbol"])
    assert not result["account_executed"] and not result["strategy_qualified"]
    assert result["independent_validation"] == "NOT_RUN" and result["paper_observation_days"] == 0
    assert accesses == [] and not Path(authority["budget_path"]).exists()
    root = service.root / "signal-scans" / result["scan_id"]
    worker = json.loads((root / "WORKER.json").read_text(encoding="utf-8"))
    assert worker["pid"] != os.getpid()
    resource = json.loads((root / "RESOURCE.json").read_text(encoding="utf-8"))
    assert resource["returncode"] == 0 and resource["resource_platform"] == os.name
    if os.name == "nt":
        assert resource["windows_job_bound"] is True
    intent = json.loads((root / "SCAN_INTENT.json").read_text(encoding="utf-8"))
    assert intent["limits"] == scan.LIMITS and (root / "INPUT.json").is_file()
    snapshot = json.loads((root / "INPUT.json").read_text(encoding="utf-8"))
    assert snapshot["bundle"]["source_identity"] == expected["bundle"]["source_identity"]
    assert snapshot["qualification"]["manifest_hash"] == hashlib.sha256(metadata_path.read_bytes()).hexdigest()
    assert intent["registration"]["original_metadata_path"] == str(metadata_path)
    assert result["registration"]["original_metadata_sha256"] != result["registration"]["snapshot_sha256"]
    assert "收益" in (root / "REPORT_CN.md").read_text(encoding="utf-8")
    reference = scan.validated_scan_snapshot(service, result)
    assert reference["metadata_only"] and reference["input_identity"] == expected["input_identity"]
    monkeypatch.setattr(scan, "run_bounded_worker", lambda *a, **k: pytest.fail("不得免费重扫"))
    repeated = call_scan(service, request)
    assert repeated["scan_identity"] == result["scan_identity"]
    assert repeated["recorded_only"] and not repeated["content_reread"] and not repeated["freshly_scanned"]
    assert not list(service.root.glob("*/TASK.json"))


def test_true_worker_reports_full_unknown_denominator_with_no_company_action_proof(tmp_path):
    service, request, authority, _ = public_universe_case(tmp_path, complete=False)
    result = call_scan(service, request)
    assert result["status"] == "SCAN_DATA_GAPS", result
    assert result["processed_target_count"] == result["unknown_target_count"] == result["target_count"] == 3
    assert result["signals_evaluated_target_count"] == result["signals_evaluated_session_count"] == 0
    assert result["scanner_identity"] is None and result["internal_computation_symbols"] == []
    assert all(row["condition_counts"] is None and row["status"] == "UNKNOWN" for row in result["per_symbol"])
    assert not Path(authority["budget_path"]).exists()


def test_all_unknown_skips_indicator_computation_without_losing_target_scope(monkeypatch):
    window, bundle = fixture()
    bundle["corporate_actions_complete"] = False
    inputs = UniverseAccountInputsV1(bundle, window, stage="SCAN")
    rule = ResearchRuleStrategyV3(proposal(), strategy_id="UNKNOWN_ONLY")
    monkeypatch.setattr(UniverseSignalScanV1, "__init__", lambda *a, **k: pytest.fail("缺证据不能计算指标"))
    value = scan._evaluate_conditions(rule, inputs)
    assert value["processed_target_count"] == value["unknown_target_count"] == 3
    assert value["signals_evaluated_target_count"] == 0
    assert all(row["condition_counts"] is None for row in value["per_symbol"])


def test_explicit_partial_action_coverage_scans_known_stocks_and_keeps_unknown_denominator():
    window, bundle = fixture()
    coverage = bundle["corporate_action_coverage"][0]
    bundle["corporate_actions_complete"] = False
    bundle["corporate_action_coverage"] = [
        {**coverage, "symbols": [symbol], "complete": symbol != "300001.SZ"}
        for symbol in window["symbols"]]
    inputs = UniverseAccountInputsV1(bundle, window, stage="SCAN")
    rule = ResearchRuleStrategyV3(proposal(), strategy_id="PARTIAL_ACTION_PROOF")
    value = scan._evaluate_conditions(rule, inputs)
    assert value["processed_target_count"] == 3
    assert value["signals_evaluated_target_count"] == 2
    assert value["unknown_target_count"] == 1
    unknown = next(row for row in value["per_symbol"] if row["symbol"] == "300001.SZ")
    assert unknown["condition_counts"] is None
    assert not inputs.coverage["account_data_ready"]
    with pytest.raises(ValueError, match="CORPORATE_ACTIONS_INCOMPLETE"):
        UniverseAccountInputsV1(bundle, window, stage="ACCOUNT")


def test_halted_cash_ex_date_is_specific_unknown_while_healthy_targets_are_evaluated():
    window, bundle = halted_ex_date_case()
    inputs = UniverseAccountInputsV1(bundle, window, stage="SCAN")
    rule = ResearchRuleStrategyV3(proposal(), strategy_id="HALTED_DIVIDEND_SCAN")
    value = scan._evaluate_conditions(rule, inputs)
    assert value["processed_target_count"] == len(window["symbols"]) == 3
    assert value["qualification_checked"] is True
    assert value["signals_evaluated_target_count"] == 2
    assert value["unknown_target_count"] == 1
    assert value["internal_computation_symbols"] == ["300001.SZ", "600000.SH"]
    rows = {row["symbol"]: row for row in value["per_symbol"]}
    unknown = rows["000001.SZ"]
    assert unknown["status"] == "UNKNOWN"
    assert unknown["reasons"] == ["CAUSAL_PRICE_EX_DATE_MISSING"]
    assert unknown["evaluated_sessions"] == 0 and unknown["unknown_sessions"] == 20
    assert unknown["condition_counts"] is None and unknown["entry_eligible_sessions"] is None
    base_window, base_bundle = fixture()
    base = scan._evaluate_conditions(rule, UniverseAccountInputsV1(base_bundle, base_window, stage="SCAN"))
    for healthy in base["per_symbol"]:
        if healthy["symbol"] != "000001.SZ":
            assert rows[healthy["symbol"]] == healthy
    assert value["signals_evaluated_session_count"] == 40
    assert inputs.bundle["events"] == bundle["events"]
    assert inputs.bar("000001.SZ", window["calendar"][65]) is None
    inputs.assert_unchanged()


def test_public_scan_does_not_turn_other_feature_failures_into_zero_condition_counts(monkeypatch):
    window, bundle = halted_ex_date_case()
    inputs = UniverseAccountInputsV1(bundle, window, stage="SCAN")
    rule = ResearchRuleStrategyV3(proposal(), strategy_id="STRICT_FEATURE_FAILURE")
    def fail(*args, **kwargs):
        raise ValueError("DATA_DEPENDENCY_NOT_MET")
    monkeypatch.setattr(rule, "build_feature_matrix", fail)
    with pytest.raises(ValueError, match="^DATA_DEPENDENCY_NOT_MET$"):
        scan._evaluate_conditions(rule, inputs)


def test_partial_action_coverage_is_frozen_in_input_identity():
    window, bundle = fixture()
    bundle["corporate_actions_complete"] = False
    coverage = bundle["corporate_action_coverage"][0]
    bundle["corporate_action_coverage"] = [
        {**coverage, "symbols": [symbol], "complete": symbol != "300001.SZ"}
        for symbol in window["symbols"]]
    inputs = UniverseAccountInputsV1(bundle, window, stage="SCAN")
    original = inputs.input_identity
    bundle["corporate_action_coverage"][0]["complete"] = False
    changed = UniverseAccountInputsV1(bundle, window, stage="SCAN")
    assert original != changed.input_identity


def test_same_window_next_open_modeled_states_are_unknown_at_previous_decision(tmp_path, monkeypatch):
    service, request, _, _ = public_universe_case(tmp_path)
    def next_open(frame):
        frame["available_at"] = "2022-12-30T09:30:00+08:00"
        return frame
    replace_registered_source(service, "states.parquet", next_open)
    synthetic_launcher(monkeypatch)
    monkeypatch.setattr(UniverseSignalScanV1, "__init__", lambda *a, **k: pytest.fail("当时不可见不能算合法条件"))
    result = call_scan(service, request)
    assert result["status"] == "SCAN_DATA_GAPS" and result["processed_target_count"] == 3
    assert result["signals_evaluated_target_count"] == 0 and result["unknown_target_count"] == 3
    assert all(row["condition_counts"] is None for row in result["per_symbol"])


def test_one_unknown_stock_kept_in_full_denominator_while_known_stocks_use_same_scanner(monkeypatch):
    window, bundle = fixture()
    bundle["states"] = bundle["states"].loc[bundle["states"].symbol != "300001.SZ"]
    inputs = UniverseAccountInputsV1(bundle, window, stage="SCAN")
    rule = ResearchRuleStrategyV3(proposal(), strategy_id="PARTIAL_QUALITY")
    original, windows = UniverseSignalScanV1.__init__, []
    def record(instance, strategy, view, **kwargs):
        windows.append(deepcopy(view.window))
        original(instance, strategy, view, **kwargs)
    monkeypatch.setattr(UniverseSignalScanV1, "__init__", record)
    result = scan._evaluate_conditions(rule, inputs)
    assert len(windows) == 1 and windows[0]["symbols"] == window["symbols"]
    assert result["processed_target_count"] == 3 and result["signals_evaluated_target_count"] == 2
    missing = next(row for row in result["per_symbol"] if row["symbol"] == "300001.SZ")
    assert missing["status"] == "UNKNOWN" and missing["condition_counts"] is None


def test_required_turn_missing_remains_unknown_instead_of_proxy_volume(tmp_path, monkeypatch):
    service, request, _, _ = public_universe_case(tmp_path)
    item = next(row for row in rule_capabilities()["indicators"] if row["id"] == "TURNOVER_RATE")
    request["rule"] = indicator_proposal(item)
    assert "turn" in ResearchRuleStrategyV3(request["rule"], strategy_id=request["strategy_id"]).requirements.fields
    synthetic_launcher(monkeypatch)
    result = call_scan(service, request)
    assert result["status"] == "SCAN_DATA_GAPS", result
    assert result["processed_target_count"] == 3 and result["signals_evaluated_target_count"] == 0
    assert all("UNIVERSE_REQUIRED_TURN_MISSING" in row["reasons"] for row in result["per_symbol"])


@pytest.mark.parametrize('kind', ['raw', 'turn', 'long_warmup'])
def test_worker_reuses_first_validated_inputs_with_actual_rule_fields_and_warmup(tmp_path, monkeypatch, kind):
    service, request, authority, _ = public_universe_case(tmp_path)
    if kind != 'raw':
        item = next(row for row in rule_capabilities()['indicators']
                    if row['id'] == ('TURNOVER_RATE' if kind == 'turn' else 'MA'))
        request['rule'] = indicator_proposal(item, {'window': 100} if kind == 'long_warmup' else None)
    strategy = ResearchRuleStrategyV3(request['rule'], strategy_id=request['strategy_id'])
    normalized = service.preview(request)['request']
    # 与旧路径首次通用准备、随后按实际规则构造的结果比较；仅使用合成行情。
    prior = service.provider.prepare(normalized['dataset_id'], symbols=normalized['symbols'],
        universe_id=normalized['universe_id'], feature_start=normalized['feature_start'],
        account_start=normalized['account_start'], account_end=normalized['account_end'],
        required_fields=(), authorization=authority['data_authorization'], stage='SCAN')
    expected = UniverseAccountInputsV1(prior['bundle'], prior['window'], stage='SCAN',
        required_fields=strategy.requirements.fields, warmup_bars=strategy.requirements.warmup_sessions)
    expected_evaluation = scan._evaluate_conditions(strategy, expected)
    made = []
    original = UniverseAccountInputsV1.__init__
    def construct(instance, *a, **kw):
        original(instance, *a, **kw)
        made.append(instance)
    monkeypatch.setattr(UniverseAccountInputsV1, '__init__', construct)
    launches = synthetic_launcher(monkeypatch)
    result = call_scan(service, request)
    assert len(launches) == len(made) == 1
    actual = made[0]
    assert actual.required_fields == frozenset(strategy.requirements.fields)
    assert actual.warmup_bars == strategy.requirements.warmup_sessions
    assert actual.input_identity == expected.input_identity == result['input_identity']
    assert actual.coverage == expected.coverage
    assert result['per_symbol'] == expected_evaluation['per_symbol']
    assert result['processed_target_count'] == 3
    root = service.root / 'signal-scans' / result['scan_id']
    snapshot = json.loads((root / 'INPUT.json').read_text(encoding='utf-8'))
    assert snapshot['qualification']['coverage'] == actual.coverage
    assert set(snapshot) == {'window', 'qualification', 'input_identity', 'snapshot_version', 'bundle', 'frames'}
    assert not Path(authority['budget_path']).exists()
    if kind == 'raw':
        assert result['status'] == 'CONDITIONS_EVALUATED'
    else:
        assert result['status'] == 'SCAN_DATA_GAPS' and result['unknown_target_count'] == 3
        assert result['signals_evaluated_session_count'] == 0
    if kind == 'turn':
        assert all('UNIVERSE_REQUIRED_TURN_MISSING' in row['reasons'] for row in result['per_symbol'])
    if kind == 'long_warmup':
        assert actual.warmup_bars > len(actual.calendar)
        assert all(row['status_counts'].get('WARMUP_INSUFFICIENT') == len(actual.calendar)
                   for row in actual.coverage['per_symbol'])


@pytest.mark.parametrize("change", ["expiry", "source", "purpose", "dataset", "dataset_type", "dates"])
def test_scan_authorization_checked_before_registered_content_or_workers(tmp_path, monkeypatch, change):
    service, request, authority, accesses = public_universe_case(tmp_path)
    preview = service.preview(request)
    if change == "expiry":
        authority["expires_at"] = "2000-01-01T00:00:00+00:00"
    elif change == "source":
        authority["source"]["origin"] = "AI_INFERRED"
    elif change == "purpose":
        authority["data_authorization"]["purpose"] = "QUALIFICATION"
    elif change == "dataset":
        authority["data_authorization"]["dataset_ids"] = ["other"]
    elif change == "dataset_type":
        authority["data_authorization"]["dataset_ids"] = "sample"
    else:
        authority["data_authorization"]["start"] = request["account_start"]
    monkeypatch.setattr(scan, "run_bounded_worker", lambda *a, **k: pytest.fail("先检查授权"))
    with pytest.raises(PermissionError, match="UNIVERSE_SCAN"):
        scan.scan_universe(service, request, preview["preview_identity"])
    assert accesses == [] and not service.root.exists()


def test_stale_preview_and_caller_subset_rejected_before_launch(tmp_path, monkeypatch):
    service, request, _, accesses = public_universe_case(tmp_path)
    identity = service.preview(request)["preview_identity"]
    monkeypatch.setattr(scan, "run_bounded_worker", lambda *a, **k: pytest.fail("预览不同不能启动"))
    request["initial_cash"] = 100000
    with pytest.raises(ValueError, match="SUBMISSION_PREVIEW_CHANGED"):
        scan.scan_universe(service, request, identity)
    request["symbols"] = ["000001.SZ"]
    with pytest.raises(ValueError, match="UNIVERSE_SUBMISSION_REQUEST_FIELDS_INVALID"):
        call_scan(service, request)
    assert accesses == []


def test_worker_interruption_has_durable_unknown_result_and_cannot_get_free_rescan(tmp_path, monkeypatch):
    service, request, authority, accesses = public_universe_case(tmp_path)
    launches = []
    def interrupted(command, **kwargs):
        launches.append(command)
        kwargs["on_started"](12345)
        return {"returncode": -9, "timed_out": True, "stdout": b"", "stderr": b""}
    monkeypatch.setattr(scan, "run_bounded_worker", interrupted)
    result = call_scan(service, request)
    assert result["status"] == "SCAN_BLOCKED" and not result["qualification_checked"]
    assert result["processed_target_count"] == 0 and result["unknown_target_count"] == 3
    assert all(row["condition_counts"] is None for row in result["per_symbol"])
    repeated = call_scan(service, request)
    assert repeated["recorded_only"] and repeated["scan_identity"] == result["scan_identity"]
    assert len(launches) == 1 and accesses == [] and not Path(authority["budget_path"]).exists()


@pytest.mark.parametrize("change", ["source", "metadata", "code", "cash", "receipt", "input"])
def test_changed_input_or_source_or_intent_cannot_relaunch_or_claim_cached_result(tmp_path, monkeypatch, change):
    service, request, _, _ = public_universe_case(tmp_path)
    launches = synthetic_launcher(monkeypatch)
    result = call_scan(service, request)
    root = service.root / "signal-scans" / result["scan_id"]
    if change == "source":
        data = service.provider._datasets["sample"][0] / "daily.parquet"
        with data.open("ab") as stream:
            stream.write(b"changed")
    elif change == "metadata":
        # 解析内容没有变化也不得冒充同一来源字节身份。
        with service.provider._metadata_paths["sample"].open("a", encoding="utf-8") as stream:
            stream.write(" ")
    elif change == "code":
        original = scan._source_files
        def changed(rule):
            hashes = original(rule)
            hashes[str(Path(scan.__file__))] = "0" * 64
            return hashes
        monkeypatch.setattr(scan, "_source_files", changed)
    elif change == "cash":
        request["initial_cash"] = 100000
    elif change == "receipt":
        (root / "SCAN_RECEIPT.json").write_text("{}", encoding="utf-8")
    else:
        with (root / "INPUT.json").open("a", encoding="utf-8") as stream:
            stream.write(" ")
    with pytest.raises(ValueError, match="UNIVERSE_SCAN"):
        call_scan(service, request)
    assert len(launches) == 1


def test_parent_death_before_resource_receipt_does_not_relaunch_same_intent(tmp_path, monkeypatch):
    service, request, _, _ = public_universe_case(tmp_path)
    def parent_died(command, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(scan, "run_bounded_worker", parent_died)
    with pytest.raises(KeyboardInterrupt):
        call_scan(service, request)
    monkeypatch.setattr(scan, "run_bounded_worker", lambda *a, **k: pytest.fail("必须先对账中断"))
    with pytest.raises(ValueError, match="UNIVERSE_SCAN_INTERRUPTED_REQUIRES_RECONCILIATION"):
        call_scan(service, request)
    assert len(list((service.root / "signal-scans").glob("*/START.json"))) == 1


def test_validated_snapshot_only_reads_bound_metadata_and_rejects_a_caller_identity(tmp_path, monkeypatch):
    service, request, _, _ = public_universe_case(tmp_path)
    synthetic_launcher(monkeypatch)
    result = call_scan(service, request)
    monkeypatch.setattr(service.provider, "prepare", lambda *a, **k: pytest.fail("引用查询不准备数据"))
    import pandas as pd
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: pytest.fail("引用查询不读取行情"))
    reference = scan.validated_scan_snapshot(service, result)
    assert reference["metadata_only"] is True and reference["input_identity"] == result["input_identity"]
    assert reference["sha256"] == hashlib.sha256(Path(reference["path"]).read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="UNIVERSE_SCAN_SNAPSHOT_RESULT_CONFLICT"):
        scan.validated_scan_snapshot(service, {**result, "input_identity": "0" * 64})
    with pytest.raises(ValueError, match="UNIVERSE_SCAN_SNAPSHOT_REFERENCE_INVALID"):
        scan.validated_scan_snapshot(service, {**result, "scan_id": "../outside"})


def test_validated_snapshot_cannot_adopt_tampered_frozen_input(tmp_path, monkeypatch):
    service, request, _, _ = public_universe_case(tmp_path)
    synthetic_launcher(monkeypatch)
    result = call_scan(service, request)
    path = service.root / "signal-scans" / result["scan_id"] / "INPUT.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="UNIVERSE_SCAN_ARCHIVED_ARTIFACT_CHANGED"):
        scan.validated_scan_snapshot(service, result)


def test_board_policy_code_change_blocks_cached_scan_and_snapshot_without_new_worker(tmp_path, monkeypatch):
    service, request, _, _ = public_universe_case(tmp_path)
    launches = synthetic_launcher(monkeypatch)
    result = call_scan(service, request)
    original = scan._sha
    monkeypatch.setattr(scan, "_sha", lambda path: "0" * 64 if path.name == "board_execution_policy_v1.py" else original(path))
    with pytest.raises(ValueError, match="UNIVERSE_SCAN_INTENT_CHANGED_REQUIRES_RECONCILIATION"):
        call_scan(service, request)
    with pytest.raises(ValueError, match="UNIVERSE_SCAN_CODE_CHANGED"):
        scan.validated_scan_snapshot(service, result)
    assert len(launches) == 1


def rewrite_scan_intent(root, change):
    """模拟落盘工件被替换并重算自身哈希，不改父进程持有的身份。"""
    path = root / "SCAN_INTENT.json"
    intent = json.loads(path.read_text(encoding="utf-8"))
    change(intent)
    intent["intent_identity"] = scan.stable_hash({k: v for k, v in intent.items() if k != "intent_identity"})
    path.write_text(json.dumps(intent, ensure_ascii=False), encoding="utf-8")
    return intent


@pytest.mark.parametrize("binding", ["missing", "wrong", "purpose", "extra"])
def test_worker_requires_exact_parent_intent_binding_before_source_access(tmp_path, monkeypatch, binding):
    service, request, authority, _ = public_universe_case(tmp_path)
    launches = []
    def launch(command, **kwargs):
        root = Path(command[-1])
        intent = json.loads((root / "SCAN_INTENT.json").read_text(encoding="utf-8"))
        expected = {"purpose": intent["scan_id"], "intent_identity": intent["intent_identity"]}
        assert kwargs["execution"] == expected
        execution = deepcopy(kwargs["execution"])
        if binding == "missing":
            execution.pop("intent_identity")
        elif binding == "wrong":
            execution["intent_identity"] = "0" * 64
        elif binding == "purpose":
            execution["purpose"] = "different_scan"
        else:
            execution["unapproved"] = True
        kwargs["on_started"](12345)
        monkeypatch.setattr(scan, "HANDSHAKE", {"execution": execution,
            "memory_mib": kwargs["memory_mib"], "wall_seconds": kwargs["wall_seconds"]})
        monkeypatch.setattr(scan, "_check_code", lambda *a: pytest.fail("身份不符不得读取源码"))
        monkeypatch.setattr(scan, "_check_registration", lambda *a: pytest.fail("身份不符不得探测登记原件"))
        with pytest.raises(PermissionError, match="UNIVERSE_SCAN_WORKER_SCOPE_CONFLICT"):
            scan._worker(root)
        launches.append(execution)
        return {"returncode": 1, "timed_out": False, "stdout": b"", "stderr": b"",
                "resource_platform": "SYNTHETIC_NOT_OS_LIMIT_EVIDENCE"}
    monkeypatch.setattr(scan, "run_bounded_worker", launch)
    result = call_scan(service, request)
    assert len(launches) == 1 and result["status"] == "SCAN_BLOCKED"
    assert result["processed_target_count"] == 0 and result["unknown_target_count"] == 3
    assert not Path(authority["budget_path"]).exists()


@pytest.mark.parametrize("field", ["root", "original_metadata_path"])
def test_worker_rejects_rehashed_registration_before_any_code_or_registered_source_access(tmp_path, monkeypatch, field):
    service, request, _, _ = public_universe_case(tmp_path)
    launches = []
    def launch(command, **kwargs):
        root = Path(command[-1])
        parent_execution = deepcopy(kwargs["execution"])
        forged = rewrite_scan_intent(root, lambda intent: intent["registration"].update(
            {field: str((tmp_path / "unregistered" / field).absolute())}))
        assert forged["intent_identity"] != parent_execution["intent_identity"]
        kwargs["on_started"](12345)
        monkeypatch.setattr(scan, "HANDSHAKE", {"execution": parent_execution,
            "memory_mib": kwargs["memory_mib"], "wall_seconds": kwargs["wall_seconds"]})
        monkeypatch.setattr(scan, "_check_code", lambda *a: pytest.fail("父身份绑定前不得读取源码"))
        monkeypatch.setattr(scan, "_check_registration", lambda *a: pytest.fail("父身份绑定前不得探测登记原件"))
        with pytest.raises(PermissionError, match="UNIVERSE_SCAN_WORKER_SCOPE_CONFLICT"):
            scan._worker(root)
        launches.append(parent_execution)
        return {"returncode": 1, "timed_out": False, "stdout": b"", "stderr": b"",
                "resource_platform": "SYNTHETIC_NOT_OS_LIMIT_EVIDENCE"}
    monkeypatch.setattr(scan, "run_bounded_worker", launch)
    result = call_scan(service, request)
    assert len(launches) == 1 and result["status"] == "SCAN_BLOCKED"
    root = service.root / "signal-scans" / result["scan_id"]
    assert not (root / "INPUT.json").exists()


@pytest.mark.parametrize("field", ["root", "original_metadata_path", "original_metadata_sha256",
    "manifest", "physical_signatures", "universe_identity"])
def test_snapshot_rebinds_real_provider_before_probing_rehashed_registration(tmp_path, monkeypatch, field):
    service, request, _, _ = public_universe_case(tmp_path)
    launches = synthetic_launcher(monkeypatch)
    result = call_scan(service, request)
    root = service.root / "signal-scans" / result["scan_id"]
    forbidden = (tmp_path / "unregistered").absolute()
    def forge(intent):
        registration = intent["registration"]
        if field == "root":
            registration[field] = str(forbidden)
        elif field == "original_metadata_path":
            registration[field] = str(forbidden / "original.json")
        elif field == "manifest":
            registration[field]["corporate_actions_complete"] = False
        elif field == "physical_signatures":
            registration[field]["unregistered.json"] = {}
        else:
            registration[field] = "0" * 64
    rewrite_scan_intent(root, forge)
    def forbid_probe(operation):
        def checked(path, *args, **kwargs):
            if path.is_relative_to(forbidden):
                pytest.fail("伪造的登记路径不得进入任何文件探测或读取")
            return operation(path, *args, **kwargs)
        return checked
    for method in ("resolve", "stat", "read_bytes"):
        monkeypatch.setattr(Path, method, forbid_probe(getattr(Path, method)))
    with pytest.raises(ValueError, match="UNIVERSE_SCAN_SNAPSHOT_REGISTRATION_CONFLICT"):
        scan.validated_scan_snapshot(service, result)
    assert len(launches) == 1


@pytest.mark.parametrize("field", ["runtime", "loader", "registration"])
def test_public_scan_request_cannot_supply_paths_or_execution_configuration(tmp_path, monkeypatch, field):
    service, request, _, accesses = public_universe_case(tmp_path)
    request[field] = {"path": str(tmp_path / "unregistered.json")}
    service.authority = lambda *a: pytest.fail("不可信字段必须在授权读取前拒绝")
    monkeypatch.setattr(scan, "_registration", lambda *a: pytest.fail("请求不得进入登记读取"))
    monkeypatch.setattr(scan, "run_bounded_worker", lambda *a, **k: pytest.fail("非法请求不得启动worker"))
    with pytest.raises(ValueError, match="UNIVERSE_SUBMISSION_REQUEST_FIELDS_INVALID"):
        call_scan(service, request)
    assert accesses == [] and not service.root.exists()
