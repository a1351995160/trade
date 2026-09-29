"""完整测试证据包：原治理消费、真实模型账户、独立离线复核与逐项反例。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from chanlun_trader.research_factory.common import canonical_json
from chanlun_trader.research_factory.etf_account_governance_v1 import StrategyBatchGovernanceV1
from chanlun_trader.research_factory.research_evidence_v1 import verify_job_evidence, reconstruct_account
from chanlun_trader.research_factory.rule_account_backend_v2 import RuleAccountBackendV2, rule_input_identity
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run
from test_historical_process_v1 import historical_fixture
from test_rule_exit_integration_v3 import proposal


def write(path, value):
    path.write_text(canonical_json(value), encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence(tmp_path, *, empty=False, exits=None):
    root = tmp_path / "account"
    root.mkdir()
    window, bundle = historical_fixture()
    rule = proposal(exits or {})
    if empty:
        rule["buy"]["args"][1]["params"]["value"] = 1000000
    strategy = ResearchRuleStrategyV3(rule, strategy_id="case")
    backend = RuleAccountBackendV2(window, initial_cash=50000)
    identity = rule_input_identity(bundle, window)
    snapshot = deepcopy(bundle)
    from chanlun_trader.research_factory.strategy_submission_v1 import frozen_frame_schemas
    schemas = frozen_frame_schemas(bundle)
    for key in ("daily", "turn", "states"):
        snapshot[key] = snapshot[key].to_dict("records")
    source = tmp_path / "INPUT.json"
    write(source, {"bundle": snapshot, "frame_schemas": schemas, "window": window, "input_identity": identity,
                   "qualification": {"test_fixture": True, "formal_eligible": False}})
    item = {"loader": "chanlun_trader.research_factory.strategy_submission_v1:load_frozen_bundle",
            "loader_kwargs": {"path": str(source.resolve()), "sha256": sha(source), "input_identity": identity},
            "source_hashes": {str(source): sha(source)}}
    plan = prepare(strategy, backend, item)
    sources = {**item["source_hashes"], **plan["strategy"]["source_hashes"], **plan["backend"]["source_hashes"]}
    job = {"root": str(root.resolve()), "plans": {"case": plan}, "items": {"case": item},
           "objective_id": "TEST_ONLY", "budget_path": str(tmp_path / "budget.json"),
           "input_identity": identity, "source_hashes": sources}
    write(root / "JOB.json", job)
    archive = root / "source-archive"
    archive.mkdir()
    for name, digest in sources.items():
        shutil.copyfile(name, archive / (digest + "_" + Path(name).name))
    governance = StrategyBatchGovernanceV1(root, job["budget_path"], job["objective_id"], job["plans"])
    governance.confirm({"origin": "USER_EXPLICIT_CURRENT_TASK", "statement": "TEST FIXTURE ONLY",
                        "approved_plan_ids": {"case": plan["plan_id"]},
                        "expires_at": (datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat()},
                       {"input_identity": identity, "novelty": {"case": {"allowed": True, "plan_id": plan["plan_id"]}}})
    governance.start("case")
    # 测试模拟执行凭证；本测试不声称实际启动受限进程或获得正式资格。
    write(root / "case_WORKER.json", {"pid": 123, "purpose": "case"})
    write(root / "case_INPUT_ACCESS.json", {"reader_pid": 123, "purpose": "case", "input_identity": identity,
                                            "loader": item["loader"], "loader_kwargs": item["loader_kwargs"]})
    result = run(strategy, backend, frame=bundle, actions=bundle["events"], input_identity=identity,
                 active_check=lambda: governance.active_execution("case"), runtime=item)
    output = root / "case_RESULT.json"
    write(output, result)
    write(root / "case_RESOURCE.json", {"returncode": 0, "timed_out": False})
    governance.settle("case", completed=True, seconds=1, result_hash=sha(output))
    write(root / "RESULTS_INDEX.json", {"items": {"case": {"result": str(output), "sha256": sha(output),
                                                           "settlement": str(root / "case_SETTLEMENT.json")}}})
    return root


def rebind_result(root, result):
    write(root / "case_RESULT.json", result)
    digest = sha(root / "case_RESULT.json")
    settlement = read(root / "case_SETTLEMENT.json")
    settlement["result_sha256"] = digest
    write(root / "case_SETTLEMENT.json", settlement)
    index = read(root / "RESULTS_INDEX.json")
    index["items"]["case"]["sha256"] = digest
    write(root / "RESULTS_INDEX.json", index)


@pytest.mark.parametrize("empty", [False, True])
def test_complete_account_and_zero_trades_verify_without_consuming_again(tmp_path, empty):
    root = evidence(tmp_path, empty=empty)
    budget = tmp_path / "budget.json"
    before = budget.read_bytes()
    verified = verify_job_evidence(root / "JOB.json", name="case")
    assert verified["status"] == "PASS", verified
    assert verified["evidence_layers"]["formal_qualification"] is False
    assert verify_job_evidence(root / "JOB.json", name="case") == verified
    assert budget.read_bytes() == before
    if empty:
        assert verified["account_audit"]["metrics"]["trade_count"] == 0


@pytest.mark.parametrize("filename,key,value", [
    ("case_START.json", "receipt_id", "wrong"),
    ("case_RESOURCE.json", "returncode", 1),
    ("case_WORKER.json", "pid", 999),
    ("case_SETTLEMENT.json", "result_sha256", "wrong"),
    ("case_INPUT_ACCESS.json", "input_identity", "wrong"),
    ("CONFIRMATION.json", "input_identity", "wrong"),
])
def test_each_binding_failure_blocks_even_when_worker_says_pass(tmp_path, filename, key, value):
    root = evidence(tmp_path)
    record = read(root / filename)
    record[key] = value
    write(root / filename, record)
    verified = verify_job_evidence(root / "JOB.json", name="case")
    assert verified["status"] == "FAIL" and not verified["advance_allowed"]


@pytest.mark.parametrize("field", ["cash", "equity"])
def test_coherent_hashes_and_positive_metrics_do_not_hide_wrong_account(tmp_path, field):
    root = evidence(tmp_path)
    result = read(root / "case_RESULT.json")
    result["daily_accounts"][0][field] += 1000
    result["reconciliation"]["passed"] = True
    rebind_result(root, result)
    verified = verify_job_evidence(root / "JOB.json", name="case")
    assert verified["status"] == "FAIL" and "DAILY_" in verified["reasons"][0]


def test_missing_settlement_is_incomplete_not_success(tmp_path):
    root = evidence(tmp_path)
    (root / "case_SETTLEMENT.json").unlink()
    assert verify_job_evidence(root / "JOB.json", name="case")["status"] == "INCOMPLETE"


def test_changed_frozen_input_and_missing_source_archive_fail_closed(tmp_path):
    root = evidence(tmp_path)
    source = tmp_path / "INPUT.json"
    source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert verify_job_evidence(root / "JOB.json", name="case")["status"] == "FAIL"
    next((root / "source-archive").iterdir()).unlink()
    assert verify_job_evidence(root / "JOB.json", name="case")["status"] == "INCOMPLETE"


def test_missing_exit_evaluation_blocks_balanced_account(tmp_path):
    root = evidence(tmp_path, exits={"stop_loss_pct": .05})
    result = read(root / "case_RESULT.json")
    result["final_account_checkpoint"]["rule_exit_states"]["case"]["evaluations"].pop()
    rebind_result(root, result)
    verified = verify_job_evidence(root / "JOB.json", name="case")
    assert verified["status"] == "FAIL" and "EXIT_EVALUATION_MISSING" in verified["reasons"][0]


def pending_exit_case(*, opening=10., prior_volume=1000., suspended=False, quantity=100):
    day, symbol = 20240103, '000001.SZ'
    bars = {(20240102, symbol): {'volume': prior_volume},
            (day, symbol): {'open': opening, 'prev_close': 10., 'volume': 1000.}}
    states = {(day, symbol): {'listed': True, 'delisted': False, 'board': 'SZ_MAIN',
              'suspension_status': 'SUSPENDED' if suspended else 'TRADING', 'st_status': 'NORMAL'}}
    order = {'order_id': 'sell', 'strategy_id': 'case', 'symbol': symbol, 'side': 'SELL',
             'lot_id': 'lot', 'quantity': 100, 'filled_quantity': quantity,
             'remaining_quantity': 100 - quantity, 'created_at': '2024-01-03T09:30:00+08:00',
             'submitted_at': '2024-01-03T09:30:00+08:00', 'eligible_at': '2024-01-03T09:30:00+08:00'}
    fills = [{'side': 'SELL', 'symbol': symbol, 'lot_id': 'lot', 'order_id': 'sell', 'quantity': quantity}] if quantity else []
    return dict(day=day, lot_id='lot', lot={'symbol': symbol, 'remaining': 100, 'bought': 20240102},
                bars=bars, states=states, fills=fills, orders={'sell': order}, strategy_id='case')


@pytest.mark.parametrize('opening,volume,quantity', [(10., 1000., 100), (11., 1000., 100),
                                                    (9., 1000., 0), (10., 500., 50), (10., 5., 0)])
def test_exit_execution_independently_checks_price_limits_and_partial_capacity(opening, volume, quantity):
    from chanlun_trader.research_factory.research_evidence_v1 import _verify_pending_exit
    args = pending_exit_case(opening=opening, prior_volume=volume, quantity=quantity)
    _verify_pending_exit(**args)


def test_exit_suspension_is_proven_by_frozen_state_without_fake_order():
    from chanlun_trader.research_factory.research_evidence_v1 import _verify_pending_exit
    args = pending_exit_case(suspended=True, quantity=0)
    args['orders'] = {}
    _verify_pending_exit(**args)


def test_exit_cannot_claim_rejection_without_objective_reason():
    from chanlun_trader.research_factory.research_evidence_v1 import _verify_pending_exit
    args = pending_exit_case(quantity=0)
    args['orders']['sell'].update(status='REJECTED', reason='SUSPENDED')
    with pytest.raises(ValueError, match='EXIT_EXECUTED_QUANTITY_CONFLICT'):
        _verify_pending_exit(**args)


def test_correct_sell_decisions_and_balanced_cash_do_not_prove_exit_execution():
    import pandas as pd
    dates, symbol = [20240102, 20240103, 20240104], '000001.SZ'
    bundle = {'daily': pd.DataFrame([dict(date=d, symbol=symbol, open=10., close=9., prev_close=10., volume=1000.) for d in dates]),
              'states': pd.DataFrame([dict(trade_date=d, symbol=symbol, listed=True, delisted=False,
                 suspension_status='TRADING', st_status='NORMAL', board='SZ_MAIN') for d in dates]), 'events': []}
    window = {'calendar': dates, 'account_start': dates[0], 'account_end': dates[-1], 'symbols': [symbol]}
    fills = [dict(trade_id='t', fill_time='2024-01-02T09:30:00+08:00', symbol=symbol, strategy_id='case',
                  side='BUY', quantity=100, price=10., fee=5., lot_id='lot')]
    traces = [dict(date=d, lot_id='lot', entry_price=10., raw_close=9., comparison_close=9.,
                   reasons=['EXIT_FIXED_COST_STOP']) for d in dates]
    result = {'daily_accounts': [dict(date=d, cash=48995., equity=49895., positions=[dict(strategy_id='case', symbol=symbol, quantity=100)]) for d in dates],
        'fills': fills, 'decisions': [dict(date=d, decisions=[dict(strategy_id='case', symbol=symbol, side='SELL', exit_lot_ids=['lot'])]) for d in dates],
        'final_account_checkpoint': {'rule_exit_states': {'case': {'evaluations': traces}},
          'economic': {'cash': 48995., 'dividend_tax_withheld': 0., 'trades': fills, 'lots': {'lot': {'remaining_quantity': 100}}, 'orders': {}}},
        'metrics': {'net_return': 49895/50000-1, 'max_drawdown': 1-49895/50000, 'total_fees': 5., 'trade_count': 1}}
    with pytest.raises(KeyError, match='EXIT_ORDER_EVIDENCE_MISSING'):
        reconstruct_account(bundle, window, result, initial_cash=50000,
            costs={'slippage_bps': 0., 'commission_rate': 0., 'min_commission': 5., 'stamp_tax_rate': 0.},
            strategy_id='case', rule={'exits': {'stop_loss_pct': .05}})


def test_actual_triggered_account_still_passes_independent_gate(tmp_path):
    root = evidence(tmp_path, exits={'take_profit_pct': .001})
    result = read(root / 'case_RESULT.json')
    assert any(fill['side'] == 'SELL' for fill in result['fills'])
    verified = verify_job_evidence(root / 'JOB.json', name='case')
    assert verified['status'] == 'PASS', verified


@pytest.mark.parametrize('volume,filled,pending_filled', [(1000., 100, 0), (1500., 150, 50), (2000., 200, 100)])
def test_whole_position_exit_independently_allocates_fifo(volume, filled, pending_filled):
    from chanlun_trader.research_factory.research_evidence_v1 import _verify_pending_exit
    args = pending_exit_case(prior_volume=volume, quantity=filled)
    args['orders']['sell'].update(lot_id=None, quantity=200, remaining_quantity=200-filled)
    args['fills'][0]['lot_id'] = None
    args['all_lots'] = {'older': {**args['lot'], 'bought': 20240101}, 'lot': args['lot']}
    args['prior_decisions'] = [{'strategy_id': 'case', 'symbol': args['lot']['symbol'], 'side': 'SELL'}]
    assert _verify_pending_exit(**args) == pending_filled


def test_whole_order_cannot_replace_lot_directed_decision():
    from chanlun_trader.research_factory.research_evidence_v1 import _verify_pending_exit
    args = pending_exit_case()
    args['orders']['sell']['lot_id'] = None
    args['fills'][0]['lot_id'] = None
    args['all_lots'] = {'lot': args['lot']}
    args['prior_decisions'] = [{'strategy_id': 'case', 'symbol': args['lot']['symbol'],
                              'side': 'SELL', 'exit_lot_ids': ['lot']}]
    with pytest.raises(KeyError, match='EXIT_ORDER_EVIDENCE_MISSING'):
        _verify_pending_exit(**args)


def test_whole_order_cannot_fake_later_lot_allocation():
    from chanlun_trader.research_factory.research_evidence_v1 import _verify_pending_exit
    args = pending_exit_case()
    args['orders']['sell'].update(lot_id=None, quantity=200, remaining_quantity=100)
    args['all_lots'] = {'older': {**args['lot'], 'bought': 20240101}, 'lot': args['lot']}
    args['prior_decisions'] = [{'strategy_id': 'case', 'symbol': args['lot']['symbol'], 'side': 'SELL'}]
    with pytest.raises(ValueError, match='EXIT_FIFO_FILL_LOT_CONFLICT'):
        _verify_pending_exit(**args)
