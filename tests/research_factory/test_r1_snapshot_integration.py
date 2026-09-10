"""限定快照接入：真实部署模块、编译器与引擎，自包含合成输入。"""
from dataclasses import replace
import hashlib
import json
import os
import sys

import pandas as pd
import pytest

from chanlun_trader.research_factory.source_dependencies import load_corrected_module
from chanlun_trader.research.strategy_candidate import StrategyCandidateSpec
from chanlun_trader.research.strategy_semantic import build_semantic_record
from chanlun_trader.research.strategy_validation import ValidationPolicyV1
from test_candidate_executable_materialization_v1 import _base_candidate


@pytest.fixture(scope="module", autouse=True)
def engine_call_evidence():
    previous = sys.getprofile()
    counts = {"synthetic_engine_run_count": 0}
    def observe(frame, event, arg):
        if event == "call" and frame.f_code.co_name == "run" and frame.f_globals.get("__name__") == "chanlun_trader.engine.engine":
            counts["synthetic_engine_run_count"] += 1
        if previous is not None:
            previous(frame, event, arg)
    sys.setprofile(observe)
    yield counts
    sys.setprofile(previous)
    print("R1_SYNTHETIC_ENGINE_EVIDENCE=" + json.dumps(counts))


def record(*, hold=3):
    payload = _base_candidate().to_dict()
    payload.update(holding_period=hold, exit_rule={"type": "FIXED_HOLD", "sessions": hold})
    candidate = StrategyCandidateSpec.create(payload)
    result = build_semantic_record(candidate, {"hypothesis_id": candidate.parent_hypothesis_id,
        "hypothesis_type": "CONTINUATION", "market_regime": []})
    # 新建测试合同明确声明排名，无阈值预筛选；未改任何冻结候选。
    return replace(result, signal_predicate=replace(result.signal_predicate,
        predicate_type="RANK_ONLY", logic="RANK_ONLY", factor_conditions=(), interaction_conditions=()))


def states(root, calendar, *, blocked=()):
    for folder, normal in (("st_state", "NORMAL"), ("suspension_state", "TRADING")):
        directory = root / folder
        directory.mkdir(parents=True, exist_ok=True)
        for symbol in ("600000.SH", "000001.SZ"):
            rows = [dict(trade_date=d, status="UNKNOWN" if (symbol, d) in blocked else normal) for d in calendar]
            (directory / ("symbol=" + symbol.replace(".", "_") + ".jsonl")).write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def inputs(tmp_path, *, count=10, start=0, blocked=(), closed_days=(), sell_volume=None):
    corrected = load_corrected_module()
    calendar = [int(d.strftime("%Y%m%d")) for d in pd.bdate_range("2024-01-02", periods=count)]
    states(tmp_path, calendar, blocked={(symbol, calendar[i]) for symbol, i in blocked})
    daily = pd.DataFrame([dict(date=d, symbol=symbol, open=10., high=10.2, low=9.8,
        close=10.1, prev_close=10., volume=(0. if i in closed_days else
            (sell_volume if sell_volume is not None and i >= start + 4 else 1000000.)), amount=10000000.)
        for i, d in enumerate(calendar) for symbol in ("600000.SH", "000001.SZ")])
    factors = pd.DataFrame([dict(date=d, symbol=symbol, volume=1000000., RETURN_5D=score,
        available_at=corrected.legacy.ts_for_date(d, 15))
        for d in calendar[start:] for symbol, score in (("600000.SH", 100.), ("000001.SZ", 90.))])
    policy = replace(ValidationPolicyV1(), initial_cash=100000., max_positions=1,
                     research_start=calendar[0], research_end=calendar[-1])
    return corrected, calendar, daily, factors, policy


def run(tmp_path, *, setup=None, candidate=None, write_evidence=False, evidence_root=None, **kwargs):
    corrected, calendar, daily, factors, policy = setup or inputs(tmp_path, **kwargs)
    result = corrected.run_corrected_candidate(tmp_path, candidate or record(), "SYNTHETIC_ONLY",
        factors, corrected.legacy.build_store(daily), calendar,
        {d: set(daily.symbol) for d in calendar}, corrected.legacy.PITStateMap(tmp_path, calendar),
        {}, {}, {}, policy, portfolio_name="BASE_RESEARCH", write_evidence=write_evidence, evidence_root=evidence_root)
    return corrected, calendar, policy, result


def test_f01_empty_dual_source_fails_closed(tmp_path):
    corrected = load_corrected_module()
    assert corrected.legacy.PITStateMap(tmp_path, [20240102]).tradable("600000.SH", 20240102)[0] is False


def test_f02_qualify_before_top_n(tmp_path):
    corrected, calendar, policy, (engine, metrics, diag) = run(tmp_path,
        blocked=[("600000.SH", i) for i in range(10)])
    assert diag["entry_signals"][0]["symbol"] == "000001.SZ"
    assert any(t.side == corrected.Side.BUY for t in engine.ledger.valid_trades)


def test_f03_mid_calendar_exit_delay(tmp_path):
    setup = inputs(tmp_path, count=110, start=99, closed_days={104, 105, 106})
    # 只在99收盘发入场，100买入，103到期，107成交。
    setup[3].drop(setup[3][setup[3].date != setup[1][99]].index, inplace=True)
    corrected, calendar, policy, (engine, metrics, diag) = run(tmp_path, setup=setup)
    sells = [t for t in engine.ledger.valid_trades if t.side == corrected.Side.SELL]
    assert sells and int(sells[0].fill_time.strftime("%Y%m%d")) == calendar[107]
    assert metrics["max_exit_delay_sessions"] == 3


def test_f04_stress_must_not_retain_base_metrics(tmp_path):
    corrected, calendar, policy, (engine, base, diag) = run(tmp_path)
    assert engine.ledger.valid_trades
    stress = corrected.recompute_cost_stress_metrics(engine, base, policy, policy.initial_cash,
        fee_mult=2., stamp_mult=2., slip_mult=2.)
    assert stress["net_return"] != base["net_return"]
    for field in ("annualized_return", "average_exposure", "unrealized_pnl"):
        assert field not in stress or stress[field] is None or stress[field] != base[field], field


@pytest.mark.parametrize("missing", ["st_state", "suspension_state", "symbol", "day", "outside", "unknown", "conflict"])
def test_pit_evidence_matrix(tmp_path, missing):
    corrected = load_corrected_module()
    calendar = [20240102, 20240103]
    states(tmp_path, calendar)
    path = tmp_path / "st_state/symbol=600000_SH.jsonl"
    if missing in {"st_state", "suspension_state"}:
        for child in (tmp_path / missing).iterdir():
            child.unlink()
    elif missing == "symbol":
        path.unlink()
    elif missing == "day":
        path.write_text(json.dumps(dict(trade_date=20240102, status="NORMAL")), encoding="utf-8")
    elif missing in {"unknown", "conflict"}:
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n" + json.dumps(dict(trade_date=20240103, status="UNKNOWN" if missing == "unknown" else "ST")))
    result = corrected.legacy.PITStateMap(tmp_path, calendar)
    assert not result.tradable("600000.SH", 20240104 if missing == "outside" else 20240103)[0]


def test_pit_complete_normal(tmp_path):
    states(tmp_path, [20240102])
    assert load_corrected_module().legacy.PITStateMap(tmp_path, [20240102]).tradable("600000.SH", 20240102)[0]


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -float("inf"), 90.])
@pytest.mark.parametrize("reverse", [False, True])
def test_rank_finite_and_stable_ties(tmp_path, score, reverse):
    setup = inputs(tmp_path)
    setup[3].loc[setup[3].symbol == "600000.SH", "RETURN_5D"] = score
    if reverse:
        setup = (*setup[:3], setup[3].iloc[::-1], setup[4])
    _, _, _, (_, _, diag) = run(tmp_path, setup=setup)
    assert diag["entry_signals"][0]["symbol"] == "000001.SZ"


def test_held_high_score_does_not_hide_backup(tmp_path):
    setup = inputs(tmp_path)
    _, calendar, _, (_, _, diag) = run(tmp_path, setup=setup)
    day_two = [item for item in diag["entry_signals"] if str(calendar[1])[:4] + "-" + str(calendar[1])[4:6] + "-" + str(calendar[1])[6:] in item["generated_at"]]
    assert day_two and day_two[0]["symbol"] == "000001.SZ"


@pytest.mark.parametrize("start", [0, 5, 99])
@pytest.mark.parametrize("delay", [0, 3])
def test_calendar_translation_and_delayed_fill(tmp_path, start, delay):
    setup = inputs(tmp_path, count=start + 12, start=start,
        closed_days=set(range(start + 5, start + 5 + delay)))
    setup[3].drop(setup[3][setup[3].date != setup[1][start]].index, inplace=True)
    _, _, _, (_, metrics, _) = run(tmp_path, setup=setup)
    assert metrics["max_exit_delay_sessions"] == delay


@pytest.mark.parametrize("count,partial", [(5, False), (10, False), (10, True)])
def test_stress_base_identity_and_open_accounting(tmp_path, count, partial):
    setup = inputs(tmp_path, count=count, sell_volume=1000. if partial else None)
    setup[3].drop(setup[3][setup[3].date != setup[1][0]].index, inplace=True)
    corrected, _, policy, (engine, base, _) = run(tmp_path, setup=setup)
    before = repr((engine.ledger.trades, engine.ledger.lots, engine.orders.orders))
    identity = corrected.recompute_cost_stress_metrics(engine, base, policy, policy.initial_cash,
        fee_mult=1., stamp_mult=1., slip_mult=1.)
    for field in ("final_equity", "realized_pnl", "unrealized_pnl", "annualized_return", "average_exposure", "total_fees"):
        assert identity[field] == pytest.approx(base[field], abs=1e-7), field
    stress = corrected.recompute_cost_stress_metrics(engine, base, policy, policy.initial_cash,
        fee_mult=2., stamp_mult=2., slip_mult=2.)
    assert stress["final_equity"] == pytest.approx(policy.initial_cash + stress["realized_pnl"] + stress["unrealized_pnl"])
    assert repr((engine.ledger.trades, engine.ledger.lots, engine.orders.orders)) == before
    if partial:
        assert any(lot.exit_state == "SELL_PENDING" for lot in engine.ledger.lots.values())


def test_independent_base_and_10k_lots(tmp_path):
    setup = inputs(tmp_path)
    setup[3].drop(setup[3][setup[3].date != setup[1][0]].index, inplace=True)
    _, _, _, (base, _, _) = run(tmp_path, setup=setup)
    small_setup = (*setup[:4], replace(setup[4], initial_cash=10000.))
    _, _, _, (small, _, _) = run(tmp_path, setup=small_setup)
    assert base.ledger is not small.ledger
    assert base.ledger.valid_trades[0].quantity > small.ledger.valid_trades[0].quantity > 0
    assert any(t.side.value == "SELL" for t in small.ledger.valid_trades)


def test_available_at_not_fabricated(tmp_path):
    setup = inputs(tmp_path)
    setup[3]["available_at"] = pd.Timestamp("2025-01-01", tz="Asia/Shanghai")
    _, _, _, (engine, _, diag) = run(tmp_path, setup=setup)
    assert not engine.ledger.valid_trades and not diag["entry_signals"]
    with pytest.raises(ValueError, match="AVAILABLE_AT_EVIDENCE_MISSING"):
        run(tmp_path, setup=(*setup[:3], setup[3].drop(columns="available_at"), setup[4]))


@pytest.mark.parametrize("change,code", [({"price_mode": "PIT_QFQ"}, "PRICE_OR_FREQUENCY"),
    ({"required_frequency": ("MINUTE",)}, "PRICE_OR_FREQUENCY"),
    ({"max_positions": 2}, "POLICY_EXECUTION_MISMATCH"),
    ({"entry_timing": {"type": "SAME_BAR"}}, "ENTRY_TIMING")])
def test_unsupported_contract_denied(tmp_path, change, code):
    candidate = record()
    candidate = replace(candidate, candidate=StrategyCandidateSpec.create(candidate.candidate.to_dict() | change))
    with pytest.raises(ValueError, match=code):
        run(tmp_path, candidate=candidate)


def test_historical_entries_reject_before_io(tmp_path):
    corrected = load_corrected_module()
    for module, names in ((corrected, ("main", "main_run", "invalidate_old_run", "create_corrected_trial_registry")),
                          (corrected.legacy, ("main", "run", "run_candidate", "verify_freezes"))):
        for name in names:
            with pytest.raises(RuntimeError, match="HISTORICAL_EXECUTION_DISABLED"):
                getattr(module, name)(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_optional_and_required_benchmark(tmp_path):
    helper = load_corrected_module().legacy
    assert helper.market_regimes(helper.load_market_index(tmp_path, helper.ResearchDataAccessGuard(), 20240102, 20240103)) == {}
    with pytest.raises(ValueError, match="BENCHMARK_REQUIRED"):
        helper.load_market_index(tmp_path, helper.ResearchDataAccessGuard(), 20240102, 20240103, required=True)


def test_composite_ranking_uses_same_compiler(tmp_path):
    setup = inputs(tmp_path)
    setup[3]["RETURN_3D"] = setup[3].symbol.map({"600000.SH": 0., "000001.SZ": 100.})
    rec = record()
    payload = rec.candidate.to_dict()
    binding = {"factor_id": "RETURN_3D", "role": "PRIMARY_ALPHA", "direction": "POSITIVE"}
    payload["factor_bindings"] = [*payload["factor_bindings"], binding]
    payload["factor_roles"] = [*payload["factor_roles"], binding]
    payload["ranking_rule"] = dict(type="COMPOSITE_PERCENTILE", weighting="EQUAL",
        components=[dict(factor_id=name, direction="DESC") for name in ("RETURN_5D", "RETURN_3D")])
    rec = replace(rec, candidate=StrategyCandidateSpec.create(payload))
    corrected, _, _, (_, _, diag) = run(tmp_path, setup=setup, candidate=rec)
    first_rows = [row for row in diag["qualified_rows"] if row["generated_at"] == diag["qualified_rows"][0]["generated_at"]]
    expected = corrected.legacy.StrategyCandidateCompilerV2().compile(rec).emit_entry_signals(first_rows)
    assert expected[0].symbol == diag["entry_signals"][0]["symbol"] == "000001.SZ"


def test_structure_exit_delay_uses_actual_trigger(tmp_path):
    setup = inputs(tmp_path, closed_days={3, 4})
    setup[3].loc[setup[3].date >= setup[1][2], "RETURN_5D"] = -1.
    rec = record(hold=5)
    rec = replace(rec, exit_predicate=replace(rec.exit_predicate, exit_type="STRUCTURE_INVALIDATION",
        logic="OR", factor_conditions=({"factor_id": "RETURN_5D", "operator": "LT", "value": 0.},)))
    corrected, _, _, (engine, metrics, _) = run(tmp_path, setup=setup, candidate=rec)
    assert any(t.side == corrected.Side.SELL for t in engine.ledger.valid_trades)
    assert metrics["max_exit_delay_sessions"] == 2


def test_tail_sell_pending_and_no_future_rows(tmp_path):
    setup = inputs(tmp_path, count=6, closed_days={5})
    setup[3].drop(setup[3][setup[3].date != setup[1][0]].index, inplace=True)
    _, _, _, (engine, metrics, _) = run(tmp_path, setup=setup)
    assert metrics["legal_pending_exit_count_at_end"] == 1
    assert any(lot.exit_state == "SELL_PENDING" for lot in engine.ledger.lots.values())
    assert not any(t.side.value == "SELL" for t in engine.ledger.valid_trades)


def test_separate_evidence_root(tmp_path):
    root = tmp_path / "inputs"
    output = tmp_path / "evidence"
    setup = inputs(root)
    with pytest.raises(ValueError, match="SEPARATE_EVIDENCE_ROOT"):
        run(root, setup=setup, write_evidence=True)
    _, _, _, (_, metrics, _) = run(root, setup=setup, write_evidence=True, evidence_root=output)
    assert (output / "orders.csv").is_file()
    assert (output / "metrics.json").is_file()
    assert metrics["evidence_dir"] == str(output)
    assert not (root / "reports").exists()


def test_hard_benchmark_gate_rejects_unknown(tmp_path):
    rec = record()
    rec = replace(rec, signal_predicate=replace(rec.signal_predicate,
        regime_conditions=({"mode": "HARD_GATE", "allowed_values": ["BULL"]},)))
    with pytest.raises(ValueError, match="BENCHMARK_REQUIRED_NOT_AVAILABLE"):
        run(tmp_path, candidate=rec)


def test_uncomputed_base_extension_is_not_stress_metric(tmp_path):
    corrected, _, policy, (engine, base, _) = run(tmp_path)
    stress = corrected.recompute_cost_stress_metrics(engine, base | {"uncomputed_extension": 123.},
        policy, policy.initial_cash, fee_mult=2., stamp_mult=2., slip_mult=2.)
    assert "uncomputed_extension" not in stress


@pytest.mark.parametrize("entry", [0, 100])
def test_metric_zero_origin_and_shift_with_real_ledger(entry):
    from chanlun_trader.engine.engine import EngineResult, BacktestRunContext
    from chanlun_trader.engine.event_log import BacktestEventLog
    from chanlun_trader.engine.fill import Fill
    from chanlun_trader.engine.ledger import PortfolioLedger
    from chanlun_trader.engine.order_manager import OrderManager
    from chanlun_trader.engine.time_types import TradingCalendar, TradingClock
    corrected = load_corrected_module()
    calendar = [int(d.strftime("%Y%m%d")) for d in pd.bdate_range("2024-01-02", periods=110)]
    ledger = PortfolioLedger(100000.)
    buy, reason = ledger.apply_fill(Fill("B", "B", "CAND", "600000.SH", corrected.Side.BUY,
        100, 10., corrected.legacy.ts_for_date(calendar[entry], 9, 30)))
    assert reason == "OK"
    ledger.lots[buy.lot_id].exit_due_index = entry + 3
    sell, reason = ledger.apply_fill(Fill("S", "S", "CAND", "600000.SH", corrected.Side.SELL,
        100, 10., corrected.legacy.ts_for_date(calendar[entry + 7], 9, 30)), lot_id=buy.lot_id)
    assert reason == "OK" and sell is not None
    ledger.snapshot(sell.fill_time)
    events = BacktestEventLog()
    result = EngineResult(ledger, events, OrderManager(events), TradingClock(TradingCalendar(calendar)), BacktestRunContext())
    assert corrected.compute_metrics_v3(result, 100000., {}, calendar, 3)["max_exit_delay_sessions"] == 3


def test_participation_contract_has_no_epsilon_allowance(tmp_path):
    setup = inputs(tmp_path)
    drifted = (*setup[:4], replace(setup[4], max_participation_rate=0.1000000001))
    with pytest.raises(ValueError, match="PARTICIPATION_CONTRACT"):
        run(tmp_path, setup=drifted)


INVALID_TIMES = [pytest.param(None, id="none"), pytest.param(pd.NaT, id="nat"),
    pytest.param(float("nan"), id="nan"), pytest.param("", id="empty"),
    pytest.param("NaT", id="nat-text"), pytest.param("not-a-date", id="invalid-text")]


def run_time_case(tmp_path, setup, request, engine_counts, *, candidate=None):
    """记录真实输入和边界计数；异常与信号放行分开保存。"""
    frames = [setup[i].copy(deep=True) for i in (2, 3)]
    files = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}
    probes = getattr(request.config, "_p3a_probes", ({},))[0]
    process = getattr(sys.modules.get("sitecustomize"), "counts", {})
    before = dict(engine_counts) | dict(probes) | dict(process)
    evidence = {"case": request.node.name, "pandas": pd.__version__,
        "dtype": str(setup[3].available_at.dtype),
        "cells": [{"index": int(i), "value": repr(v), "type": type(v).__name__}
                  for i, v in setup[3].available_at.items()]}
    writes = {"active": True, "calls": 0}
    def audit(event, args):
        if writes["active"] and event == "open":
            mode, flags = args[1:3]
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
                writes["calls"] += 1
    sys.addaudithook(audit)
    try:
        result = run(tmp_path, setup=setup, candidate=candidate)
        engine, metrics, diag = result[3]
        evidence.update(entry_signals=diag["entry_signals"],
            orders=[{"symbol": o.symbol, "side": o.side.value} for o in engine.orders.orders.values()],
            exit_decisions=[{"symbol": e.symbol, **e.payload} for e in engine.event_log if e.event_type == "EXIT_DECISION"],
            diagnostics=metrics["signal_diagnostics"])
        return result
    except Exception as exc:
        evidence.update(exception=type(exc).__name__ + ": " + str(exc), result="NOT_RETURNED")
        raise
    finally:
        writes["active"] = False
        after = dict(engine_counts) | dict(probes) | dict(process)
        evidence["counter_delta"] = {k: v - before[k] for k, v in after.items()}
        evidence["runtime_write_calls"] = writes["calls"]
        print("R1_AVAILABLE_AT_EVIDENCE=" + json.dumps(evidence, default=str, sort_keys=True))
        for actual, expected in zip((setup[2], setup[3]), frames):
            pd.testing.assert_frame_equal(actual, expected)
        assert files == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}
        assert writes["calls"] == 0


@pytest.mark.parametrize("value", INVALID_TIMES)
@pytest.mark.parametrize("scope", ["mixed", "all"])
def test_invalid_available_at_entry(tmp_path, request, engine_call_evidence, value, scope):
    setup = inputs(tmp_path)
    # object 保留原始单元格；另有 native dtype 用例记录 pandas 自动规范化。
    setup[3]["available_at"] = setup[3].available_at.astype(object)
    indices = setup[3].index if scope == "all" else [0]
    setup[3].loc[indices, "available_at"] = value
    _, calendar, _, (engine, metrics, diag) = run_time_case(tmp_path, setup, request, engine_call_evidence)
    if scope == "all":
        assert not diag["entry_signals"] and not engine.orders.orders and not engine.ledger.valid_trades
    else:
        assert diag["entry_signals"][0]["symbol"] == "000001.SZ"
        assert all(row["symbol"] != "600000.SH" for row in diag["qualified_rows"]
                   if row["current_session_index"] == 0)
        assert engine.ledger.valid_trades[0].symbol == "000001.SZ"
    assert metrics["signal_diagnostics"]["INVALID_FACTOR_AVAILABLE_AT"] == len(indices)


@pytest.mark.parametrize("value", INVALID_TIMES[:3])
def test_invalid_available_at_native_dtype(tmp_path, request, engine_call_evidence, value):
    setup = inputs(tmp_path)
    setup[3].loc[:, "available_at"] = value
    _, _, _, (engine, metrics, diag) = run_time_case(tmp_path, setup, request, engine_call_evidence)
    assert not diag["entry_signals"] and not engine.orders.orders
    assert metrics["signal_diagnostics"]["INVALID_FACTOR_AVAILABLE_AT"] == len(setup[3])


@pytest.mark.parametrize("value", INVALID_TIMES + [pytest.param("2025-01-01", id="future")])
@pytest.mark.parametrize("exit_type", ["STRUCTURE_INVALIDATION", "FIXED_HOLD"])
def test_invalid_available_at_held_exit(tmp_path, request, engine_call_evidence, value, exit_type):
    setup = inputs(tmp_path)
    setup[3].loc[setup[3].date >= setup[1][2], "RETURN_5D"] = -1.
    rec = record(hold=5)
    if exit_type == "STRUCTURE_INVALIDATION":
        rec = replace(rec, exit_predicate=replace(rec.exit_predicate, exit_type=exit_type,
            logic="OR", factor_conditions=({"factor_id": "RETURN_5D", "operator": "LT", "value": 0.},)))
    setup[3]["available_at"] = setup[3].available_at.astype(object)
    setup[3].loc[setup[3].date >= setup[1][2], "available_at"] = value
    _, calendar, _, (engine, metrics, _) = run_time_case(tmp_path, setup, request, engine_call_evidence, candidate=rec)
    assert any(t.side.value == "BUY" for t in engine.ledger.valid_trades)
    decisions = [e for e in engine.event_log if e.event_type == "EXIT_DECISION"]
    assert all(e.payload["reason_code"] != "EXIT_DUE_STRUCTURE_INVALIDATION" for e in decisions)
    sells = [t for t in engine.ledger.valid_trades if t.side.value == "SELL"]
    if value == "2025-01-01":
        # 既有未来时间会阻断退出，不能借本轮改动改变它。
        assert not decisions and not sells
    else:
        assert decisions and sells
        assert int(sells[0].fill_time.strftime("%Y%m%d")) == calendar[7]
        assert decisions[0].payload["reason_code"] == "EXIT_DUE_FIXED_HOLD"
        assert metrics["signal_diagnostics"]["INVALID_FACTOR_AVAILABLE_AT"] > 0


@pytest.mark.parametrize("time_form", ["same-day", "history", "naive", "utc", "future"])
def test_valid_available_at_controls(tmp_path, request, engine_call_evidence, time_form):
    setup = inputs(tmp_path)
    setup[3].loc[setup[3].date >= setup[1][2], "RETURN_5D"] = -1.
    rec = record(hold=5)
    rec = replace(rec, exit_predicate=replace(rec.exit_predicate, exit_type="STRUCTURE_INVALIDATION",
        logic="OR", factor_conditions=({"factor_id": "RETURN_5D", "operator": "LT", "value": 0.},)))
    if time_form == "history":
        setup[3]["available_at"] -= pd.Timedelta(days=1)
    elif time_form == "naive":
        setup[3]["available_at"] = setup[3].available_at.dt.tz_localize(None)
    elif time_form == "utc":
        setup[3]["available_at"] = setup[3].available_at.dt.tz_convert("UTC")
    elif time_form == "future":
        setup[3]["available_at"] = pd.Timestamp("2025-01-01", tz="Asia/Shanghai")
    _, calendar, _, (engine, metrics, diag) = run_time_case(tmp_path, setup, request, engine_call_evidence, candidate=rec)
    assert metrics["signal_diagnostics"].get("INVALID_FACTOR_AVAILABLE_AT", 0) == 0
    if time_form == "future":
        assert not diag["entry_signals"] and not engine.orders.orders
    else:
        assert diag["entry_signals"][0]["symbol"] == "600000.SH"
        sells = [t for t in engine.ledger.valid_trades if t.side.value == "SELL"]
        assert int(sells[0].fill_time.strftime("%Y%m%d")) == calendar[3]
        assert any(e.payload["reason_code"] == "EXIT_DUE_STRUCTURE_INVALIDATION"
                   for e in engine.event_log if e.event_type == "EXIT_DECISION")
