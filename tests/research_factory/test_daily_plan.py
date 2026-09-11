"""每日预览与真实runner信号/持仓退出对照；不授予策略使用资格。"""
from copy import deepcopy

import pandas as pd
import pytest

from r1_caller_fixture import fixture
from test_r1_batch_caller_inputs import prepare
from chanlun_trader.engine.fill import Fill
from chanlun_trader.engine.ledger import PortfolioLedger
from chanlun_trader.engine.signal import Side
from chanlun_trader.engine.portfolio_exit import PortfolioExitEvaluatorV1
from chanlun_trader.research_factory.daily_plan import DailyPlanArchiveV1, account_identity, preview_daily_plan
from chanlun_trader.research_factory.common import stable_hash


def setup(root):
    case = fixture(root)
    return case, prepare(case), PortfolioLedger(case[1].initial_cash)


def timestamp(day):
    return pd.Timestamp(str(day), tz="Asia/Shanghai") + pd.Timedelta(hours=15)


def preview(case, inputs, account, index=0, plan_at=None):
    _, policy, contract, record, _ = case
    return preview_daily_plan(record, contract, policy, inputs, account,
        plan_at=timestamp(inputs["exec_calendar"][index]) if plan_at is None else plan_at)


def test_daily_signals_match_actual_runner_and_leave_account_unchanged(tmp_path):
    case, inputs, account = setup(tmp_path)
    caller, policy, _, record, _ = case
    before = account_identity(account)
    plan = preview(case, inputs, account)
    actual = caller._invoke_runner(policy, record, "D1_REFERENCE", inputs, portfolio_name="BASE_RESEARCH")
    day = str(timestamp(inputs["exec_calendar"][0]))
    expected = [(item["symbol"], item["score"]) for item in actual["diagnostics"]["entry_signals"] if item["generated_at"] == day]
    assert expected
    assert [(item["symbol"], item["score"]) for item in plan["entries"]] == expected
    assert plan["execution_ready"] is False
    assert sum(item["quantity"] * item["reference_price"] + item["estimated_fee"] for item in plan["entries"]) <= account.available_cash()
    assert plan["usage_qualification"] == "NOT_VERIFIED"
    assert plan["plan_id"] == preview(case, inputs, account)["plan_id"]
    assert account_identity(account) == before


def test_cash_and_holdings_change_plan_identity(tmp_path):
    case, inputs, account = setup(tmp_path)
    initial = preview(case, inputs, account)
    account.cash = 50
    poor = preview(case, inputs, account)
    assert initial["plan_id"] != poor["plan_id"]
    assert poor["entries"] and all(item["action"] == "NO_TRADE" for item in poor["entries"])


def test_real_ledger_lot_exit_matches_shared_evaluator(tmp_path):
    case, inputs, account = setup(tmp_path)
    candidate = case[3].candidate
    days = inputs["exec_calendar"]
    buy = timestamp(days[1]).normalize() + pd.Timedelta(hours=9, minutes=30)
    trade, reason = account.apply_fill(Fill("F1", "O1", candidate.candidate_id, "000001.SZ", Side.BUY, 100, 10, buy))
    assert trade is not None, reason
    before = account_identity(account)
    same_day = preview(case, inputs, account, index=1)
    assert same_day["holdings"][0]["sellable_quantity"] == 0
    due_index = 1 + candidate.holding_period
    plan = preview(case, inputs, account, index=due_index)
    lot = deepcopy(next(iter(account.lots.values())))
    lot.entry_session_index = 1
    expected = PortfolioExitEvaluatorV1(candidate_id=candidate.candidate_id, portfolio_id=candidate.candidate_id,
        contract={"exit_type": "FIXED_HOLD", "fixed_holding_sessions": candidate.holding_period}).evaluate(
            [lot], days[due_index], due_index, {}, timestamp(days[due_index]))
    assert expected and plan["holdings"][0]["reason"] == expected[0].reason_code
    assert plan["holdings"][0]["action"] == "EXIT"
    assert account_identity(account) == before


@pytest.mark.parametrize("value,code", [(pd.NaT, "PLAN_TIME_AWARE_REQUIRED"),
    ("2025-07-01", "PLAN_TIME_AWARE_REQUIRED"), ("2025-07-03T14:59:00+08:00", "PLAN_T_CLOSE_REQUIRED"),
    ("2030-01-01T15:00:00+08:00", "PLAN_OUTSIDE_FROZEN_WINDOW")])
def test_invalid_plan_time_is_rejected(tmp_path, value, code):
    case, inputs, account = setup(tmp_path)
    with pytest.raises(ValueError, match=code):
        preview(case, inputs, account, plan_at=value)


@pytest.mark.parametrize("missing", [False, True])
def test_future_or_nat_factor_never_generates_entry(tmp_path, missing):
    case, inputs, account = setup(tmp_path)
    factors = inputs["factor_values"]
    factors["available_at"] = pd.to_datetime(factors.available_at)
    rows = factors.date == inputs["exec_calendar"][0]
    factors.loc[rows, "available_at"] = pd.NaT if missing else timestamp(inputs["exec_calendar"][1])
    plan = preview(case, inputs, account)
    assert not plan["entries"]
    assert plan["status"] == "NOT_READY"
    assert plan["execution_ready"] is False


@pytest.mark.parametrize("missing", ["PIT", "FACTOR"])
def test_missing_day_inputs_are_not_zero_signal_success(tmp_path, missing):
    case, inputs, account = setup(tmp_path)
    day = inputs["exec_calendar"][0]
    if missing == "PIT":
        del inputs["universe"][day]
    else:
        inputs["factor_values"] = inputs["factor_values"].loc[inputs["factor_values"].date != day]
    with pytest.raises(ValueError, match="PLAN_PIT_NOT_READY|PLAN_FACTOR_COVERAGE"):
        preview(case, inputs, account)


def test_archive_is_immutable_and_marks_account_changes_stale(tmp_path):
    case, inputs, account = setup(tmp_path / "inputs")
    store = DailyPlanArchiveV1(tmp_path / "plans")
    assert not store.path.exists()
    plan = preview(case, inputs, account)
    path = store.publish(plan)
    old_bytes = path.read_bytes()
    assert store.publish(plan) == path
    account.cash = 50
    current = preview(case, inputs, account)
    assert store.compare(plan["plan_id"], current)["changed"] == ["account_identity"]
    store.publish(current)
    assert path.read_bytes() == old_bytes
    assert len(list(store.path.glob("*.json"))) == 2
    tampered = deepcopy(plan)
    tampered["execution_ready"] = True
    with pytest.raises(ValueError, match="PLAN_ARCHIVE_IDENTITY_CONFLICT"):
        store.publish(tampered)
    path.write_text('{"plan_id":"corrupt"}', encoding="utf-8")
    with pytest.raises(ValueError, match="PLAN_ARCHIVE_IDENTITY_CONFLICT"):
        store.publish(plan)


@pytest.mark.parametrize("field", ["entries", "holdings", "readiness_reasons", "signal_diagnostics"])
def test_valid_different_content_is_never_reported_current(tmp_path, field):
    case, inputs, account = setup(tmp_path / "inputs")
    store = DailyPlanArchiveV1(tmp_path / "plans")
    plan = preview(case, inputs, account)
    archived = store.publish(plan)
    before = archived.read_bytes()
    current = deepcopy(plan)
    # 构造比较接口允许的有效内容身份，不声称策略引擎产生不同收益或信号。
    if field == "entries":
        assert current[field]
        current[field][0]["quantity"] += 100
    elif field == "holdings":
        current[field].append({"lot_id": "SYNTHETIC_COMPARISON", "action": "HOLD"})
    elif field == "readiness_reasons":
        current[field].append("NEXT_SESSION_NOT_AVAILABLE")
    else:
        current[field]["comparison_probe"] = 1
    current["plan_id"] = "PLAN_" + stable_hash({key: value for key, value in current.items() if key != "plan_id"})
    result = store.compare(plan["plan_id"], current)
    assert result["status"] == "STALE", result
    assert "plan_content" in result["changed"]
    assert archived.read_bytes() == before
    assert store.compare(plan["plan_id"], preview(case, inputs, account))["status"] == "CURRENT_RESEARCH_PREVIEW"


def test_source_identity_covers_plan_compiler_helpers_and_costs(tmp_path, monkeypatch):
    import shutil
    from chanlun_trader.research_factory import daily_plan
    case, inputs, account = setup(tmp_path / "inputs")
    old = preview(case, inputs, account)
    files = old["source_identity"]["files"]
    assert {"corrected", "helper", "daily_plan", "research/strategy_semantic.py",
        "engine/portfolio_exit.py", "engine/sizing.py", "engine/fee.py", "engine/slippage.py"} <= files.keys()
    # 只复制源码以核验部署身份变化；不改运行中的费用算法或合成输入。
    copy_root = tmp_path / "source-copy"
    for directory in ("engine", "research"):
        shutil.copytree(daily_plan.SOURCE_ROOT / f"src/chanlun_trader/{directory}",
            copy_root / f"src/chanlun_trader/{directory}", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(daily_plan, "SOURCE_ROOT", copy_root)
    assert preview(case, inputs, account)["plan_id"] == old["plan_id"]
    fee = copy_root / "src/chanlun_trader/engine/fee.py"
    fee.write_bytes(fee.read_bytes() + b"\n# synthetic source identity probe\n")
    current = preview(case, inputs, account)
    assert current["entries"] == old["entries"]
    assert current["source_identity"]["files"]["engine/fee.py"] != files["engine/fee.py"]
    archive = DailyPlanArchiveV1(tmp_path / "plans")
    path = archive.publish(old)
    before = path.read_bytes()
    assert archive.compare(old["plan_id"], current)["changed"] == ["source_identity"]
    assert path.read_bytes() == before
