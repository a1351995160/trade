"""固定验收的编排边界；工程夹具不授予真实数据或策略资格。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.exploration_governance import immutable
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from scripts.run_full_universe_acceptance_v1 import (
    CONFIG_VERSION, fixed_acceptance_requests_v1, run_full_universe_acceptance_v1,
)
from test_universe_submission_v1 import public_universe_case


def acceptance_case(tmp_path, *, complete=True):
    service, request, original, accesses = public_universe_case(tmp_path, complete=complete)
    service.capabilities = lambda: capabilities(data_catalog=service.provider.catalog())
    config = {"version": CONFIG_VERSION, "dataset_id": request["dataset_id"],
        "universe_id": request["universe_id"], "feature_start": request["feature_start"],
        "account_start": request["account_start"], "account_end": request["account_end"],
        "authorization_refs": {"single_indicator": "single", "multi_indicator": "multi"}}
    authorities = {}
    for case, value in fixed_acceptance_requests_v1(config, service.capabilities()).items():
        preview = service.preview(value)
        permit = {key: preview["request"][key] for key in (
            "initial_cash", "symbols", "feature_start", "account_start", "account_end",
            "max_positions", "max_symbol_exposure_bps", "costs", "benchmark")}
        authorities[config["authorization_refs"][case]] = {**deepcopy(original),
            "account_authorization": {**permit, "purpose": "FROZEN_PUBLIC_ACCOUNT_PLANS",
                "rule_identity": preview["rule_identity"], "max_account_jobs": 2}}
    service.authority = lambda ref: deepcopy(authorities[ref])
    return service, config, authorities, accesses


def test_two_rules_are_fixed_before_market_access_with_global_50k_and_controls(tmp_path):
    service, config, _, accesses = acceptance_case(tmp_path)
    requests = fixed_acceptance_requests_v1(config, service.capabilities())
    assert set(requests) == {"single_indicator", "multi_indicator"}
    assert {i["id"] for i in requests["single_indicator"]["rule"]["indicator_instances"]} == {"MA"}
    assert {i["id"] for i in requests["multi_indicator"]["rule"]["indicator_instances"]} == {
        "MA", "RSI", "ROLLING_VOLATILITY"}
    for value in requests.values():
        assert "symbols" not in value and value["initial_cash"] == 50000
        assert value["costs"] == ["BASE", "STRESS"]
        assert value["benchmark"] == "CASH_AND_PRICE_REFERENCE"
        assert value["rule"]["exits"]["stop_loss_pct"] == .08
    assert requests["multi_indicator"]["rule"]["market_filter"] is not None and accesses == []


@pytest.mark.parametrize("field", ["symbols", "initial_cash", "factory", "budget_path", "positive_return_required"])
def test_acceptance_config_cannot_override_scope_rules_money_or_budget(tmp_path, field):
    service, config, _, accesses = acceptance_case(tmp_path)
    config[field] = "override"
    with pytest.raises(ValueError, match="FULL_UNIVERSE_ACCEPTANCE_CONFIG_INVALID"):
        fixed_acceptance_requests_v1(config, service.capabilities())
    assert accesses == []


def test_default_diagnosis_freezes_rules_without_account_execution_or_budget(tmp_path, monkeypatch):
    service, config, authorities, accesses = acceptance_case(tmp_path)
    report_root = tmp_path / "acceptance"
    original = service.provider.prepare
    def prepare(*args, **kwargs):
        frozen = json.loads((report_root / "FROZEN_ACCEPTANCE.json").read_text(encoding="utf-8"))
        assert set(frozen["requests"]) == {"single_indicator", "multi_indicator"}
        assert frozen["criteria"]["positive_return_required"] is False
        assert kwargs["stage"] == "SCAN" and len(kwargs["symbols"]) == 3
        return original(*args, **kwargs)
    monkeypatch.setattr(service.provider, "prepare", prepare)
    for method in ("freeze", "approve", "start"):
        monkeypatch.setattr(service, method, lambda *args, **kwargs: pytest.fail("诊断不能执行账户"))
    result = run_full_universe_acceptance_v1(service, config, report_root)
    assert result["status"] == "DIAGNOSED_REQUIRES_EXECUTION"
    assert not result["strategy_qualified"] and result["real_evidence"] == "REAL_NOT_ACCEPTED"
    assert all(row["target_count"] == 3 for row in result["cases"].values())
    assert all(not Path(auth["budget_path"]).exists() for auth in authorities.values())
    assert accesses and Path(result["report_path"]).is_file() and Path(result["human_report_path"]).is_file()


def test_diagnosed_gaps_do_not_shrink_to_available_stocks_or_execute(tmp_path, monkeypatch):
    service, config, authorities, _ = acceptance_case(tmp_path, complete=False)
    for method in ("freeze", "approve", "start"):
        monkeypatch.setattr(service, method, lambda *args, **kwargs: pytest.fail("有缺口不能进入账户"))
    result = run_full_universe_acceptance_v1(service, config, tmp_path / "acceptance", execute=True)
    assert result["status"] == "DATA_GAPS"
    assert all(row["target_count"] == 3 and row["diagnosis"]["coverage"]["gaps"]
               for row in result["cases"].values())
    assert not result["strategy_qualified"] and not list(service.root.glob("*/TASK.json"))
    assert all(not Path(auth["budget_path"]).exists() for auth in authorities.values())


def test_new_rule_or_window_cannot_replace_existing_frozen_acceptance(tmp_path):
    service, config, _, accesses = acceptance_case(tmp_path)
    root = tmp_path / "acceptance"
    run_full_universe_acceptance_v1(service, config, root)
    count = len(accesses)
    changed = deepcopy(config)
    changed["account_start"] = 20220329
    with pytest.raises(ValueError, match="EXPLORATION_IMMUTABLE_CONFLICT"):
        run_full_universe_acceptance_v1(service, changed, root)
    assert len(accesses) == count


def test_missing_board_denominator_is_refused_before_data_access(tmp_path, monkeypatch):
    service, config, _, accesses = acceptance_case(tmp_path)
    original = service.preview
    def preview(request):
        result = original(request)
        result["data_metadata"]["by_board"]["CHINEXT"]["target_count"] = 0
        return result
    monkeypatch.setattr(service, "preview", preview)
    with pytest.raises(ValueError, match="THREE_BOARD_TARGET_REQUIRED"):
        run_full_universe_acceptance_v1(service, config, tmp_path / "acceptance")
    assert accesses == []


def test_partial_public_freeze_requires_original_recovery_without_free_trial(tmp_path, monkeypatch):
    service, config, authorities, _ = acceptance_case(tmp_path)
    root = tmp_path / "acceptance"
    diagnosed = run_full_universe_acceptance_v1(service, config, root)
    assert diagnosed["status"] == "DIAGNOSED_REQUIRES_EXECUTION"
    plan = json.loads((root / "FROZEN_ACCEPTANCE.json").read_text(encoding="utf-8"))
    task_id = plan["bindings"]["single_indicator"]["task_id"]
    immutable(service.root / task_id / "FREEZE_INTENT.json", {"automatic_retry": False})
    for method in ("freeze", "approve", "start"):
        monkeypatch.setattr(service, method, lambda *args, **kwargs: pytest.fail("不创建替代任务"))
    result = run_full_universe_acceptance_v1(service, config, root, execute=True)
    assert result["status"] == "RECOVERY_REQUIRED"
    assert not list(service.root.glob("*/TASK.json"))
    assert all(not Path(auth["budget_path"]).exists() for auth in authorities.values())


def test_executor_uses_public_freeze_approval_and_start_instead_of_private_matcher(tmp_path, monkeypatch):
    """只验证调用顺序，假的 start 回执不能被当成真实行情验收。"""
    service, config, _, _ = acceptance_case(tmp_path)
    calls, saved = [], {}
    def freeze(request, identity):
        calls.append("freeze")
        preview = service.preview(request)
        diagnosis = service.diagnose(request, identity)
        objective = service.authority(request["authorization_ref"])["objective_id"]
        task = {"task_id": stable_hash({"preview": identity, "objective_id": objective}),
                "preview_identity": identity, "objective_id": objective, "input_identity": diagnosis["input_identity"]}
        saved[task["task_id"]] = (task, preview)
        immutable(service.root / task["task_id"] / "TASK.json", task)
        return task
    def approval_preview(task_id):
        calls.append("approval_preview")
        task, preview = saved[task_id]
        return {**task, "request": preview["request"], "rule_identity": preview["rule_identity"],
                "plan_ids": {preview["request"]["strategy_id"] + "_BASE": "a" * 64,
                             preview["request"]["strategy_id"] + "_STRESS": "b" * 64}}
    def approve(task_id, identity):
        calls.append("approve")
        assert saved[task_id][0]["preview_identity"] == identity
        return {"task_id": task_id, "status": "APPROVED", "strategy_qualified": False}
    def start(task_id):
        calls.append("start")
        return {"task_id": task_id, "status": "ACCOUNT_VERIFIED", "strategy_qualified": False,
                "verification": {"advance_allowed": True}, "reports": None}
    monkeypatch.setattr(service, "freeze", freeze)
    monkeypatch.setattr(service, "approval_preview", approval_preview)
    monkeypatch.setattr(service, "approve", approve)
    monkeypatch.setattr(service, "start", start)
    result = run_full_universe_acceptance_v1(service, config, tmp_path / "acceptance", execute=True)
    assert calls == ["freeze", "approval_preview", "approve", "start"] * 2
    assert result["status"] == "ACCOUNT_VERIFIED" and result["real_evidence"] == "REAL_NOT_ACCEPTED"
    assert not result["strategy_qualified"] and result["independent_validation"] == "NOT_RUN"


def test_three_board_fixed_rules_really_use_public_workers_and_independent_account_audit(tmp_path):
    """四个正常/压力作业走正式服务，不替换 worker、撮合或核账实现。"""
    from chanlun_trader.research_factory.budget import SearchBudgetRegistryV1
    service, config, authorities, _ = acceptance_case(tmp_path)
    root = tmp_path / "acceptance"
    result = run_full_universe_acceptance_v1(service, config, root, execute=True)
    assert result["status"] == "ACCOUNT_VERIFIED", result
    assert result["real_evidence"] == "REAL_NOT_ACCEPTED" and result["strategy_qualified"] is False
    signatures = {}
    for case, row in result["cases"].items():
        assert row["target_count"] == 3 and row["result"]["verification"]["advance_allowed"] is True
        task = service._task(row["task_id"])
        job = json.loads(Path(task["job_path"]).read_text(encoding="utf-8"))
        assert len(job["plans"]) == 2
        for name, item in job["items"].items():
            assert item["backend_options"]["initial_cash"] == 50000
            assert "300001.SZ" in item["backend_options"]["window"]["symbols"]
            account = Path(task["job_path"]).parent
            settlement = json.loads((account / (name + "_SETTLEMENT.json")).read_text(encoding="utf-8"))
            assert settlement["completed"] is True and settlement["error"] is None
            account_result = json.loads((account / (name + "_RESULT.json")).read_text(encoding="utf-8"))
            assert account_result["reconciliation"]["passed"] is True
            signatures[name] = settlement["result_sha256"]
            assert (account / (name + "_REPORT.md")).is_file()
    budget_auth = next(iter(authorities.values()))
    before = SearchBudgetRegistryV1(budget_auth["objective_id"], budget_auth["budget_path"]).head_hash
    repeated = run_full_universe_acceptance_v1(service, config, root, execute=True)
    assert repeated["run_identity"] == result["run_identity"]
    assert SearchBudgetRegistryV1(budget_auth["objective_id"], budget_auth["budget_path"]).head_hash == before
    for row in repeated["cases"].values():
        task = service._task(row["task_id"])
        for name in task["plan_ids"]:
            settlement = json.loads((Path(task["job_path"]).parent / (name + "_SETTLEMENT.json")).read_text(encoding="utf-8"))
            assert settlement["result_sha256"] == signatures[name]
