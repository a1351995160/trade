"""以原账户实际回调为对照，验证固定合同预览与执行一致且无副作用。"""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import pytest

from test_degraded_execution_v2 import degraded
from chanlun_trader.engine.fill import Fill
from chanlun_trader.engine.ledger import PortfolioLedger
from chanlun_trader.engine.signal import Side
from chanlun_trader.engine.time_types import TradingCalendar
from chanlun_trader.research_factory.baostock_account_v1 import CONTRACT, run_baostock_account
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.daily_plan import account_identity
from chanlun_trader.research_factory.fixed_account_preview import preview_fixed_account_plan
from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules


def bundle():
    result = degraded()
    result.contract_identity = stable_hash(CONTRACT)
    result.daily['adjustflag'] = '3'
    result.ready_factors['signal_version'] = CONTRACT['signal_version']
    return result


def at(day, close=False):
    return pd.Timestamp(str(day) + (' 15:30' if close else ' 09:30'), tz='Asia/Shanghai')


def preview(b, account, rows, *, day=20220802, close=False, hazards=None, unsupported=None, **overrides):
    args = dict(plan_at=at(day, close), account_stage='AFTER_CLOSE' if close else 'POST_OPEN_ORDERS',
        contract_identity=b.contract_identity, input_identity=b.input_identity)
    args.update(overrides)
    return preview_fixed_account_plan(CONTRACT, TradingCalendar(b.calendar), account, rows,
        b.hazards if hazards is None else hazards, {} if unsupported is None else unsupported, **args)


def first_rows(b):
    return b.ready_factors.loc[b.ready_factors.timestamp == 20220801].copy()


@pytest.mark.parametrize('scenario', ['normal', 'hazard', 'late', 'partial'])
def test_preview_matches_actual_engine_at_each_open_and_close(monkeypatch, scenario):
    b = bundle()
    if scenario == 'hazard':
        b.hazards = {s: [20220803] for s in b.daily.symbol.unique()}
    elif scenario == 'late':
        b.ready_factors['effective_available_at'] = at(20240731)
    elif scenario == 'partial':
        b.hazards = {s: [20220809] for s in b.daily.symbol.unique()}
        b.states.loc[b.states.trade_date == '20220808', 'suspension_status'] = 'SUSPENDED'
    original_entries, original_exits = FixedAccountRules.entries, FixedAccountRules.exits
    opens, closes = [], []

    def entries(self, rows, calendar, ledger, hazards, unsupported, timestamp):
        before = account_identity(ledger)
        decision = original_entries(self, rows, calendar, ledger, hazards, unsupported, timestamp)
        with monkeypatch.context() as patch:
            patch.setattr(FixedAccountRules, 'entries', original_entries)
            plan = preview(b, ledger, rows, day=int(timestamp.strftime('%Y%m%d')),
                hazards=hazards, unsupported=unsupported)
        assert plan['entry_signals'] == [asdict(item) for item in decision.signals]
        assert plan['entry_checks'] == decision.checks
        assert plan['ranking'] == decision.ranking
        assert account_identity(ledger) == before
        assert plan['execution_ready'] is False
        opens.append(plan)
        return decision

    def exits(self, calendar, ledger, unsupported, timestamp):
        snapshot = deepcopy(ledger)
        before = account_identity(snapshot)
        decisions = original_exits(self, calendar, ledger, unsupported, timestamp)
        with monkeypatch.context() as patch:
            patch.setattr(FixedAccountRules, 'exits', original_exits)
            plan = preview(b, snapshot, None, day=int(timestamp.strftime('%Y%m%d')),
                close=True, unsupported=unsupported)
        assert plan['exit_decisions'] == [asdict(item) for item in decisions]
        assert account_identity(snapshot) == before
        assert not plan['entry_signals']
        closes.append(plan)
        return decisions

    monkeypatch.setattr(FixedAccountRules, 'entries', entries)
    monkeypatch.setattr(FixedAccountRules, 'exits', exits)
    result = run_baostock_account(b, ('SYNTHETIC', False))
    assert len(opens) == len(b.calendar)
    assert len(closes) == len(b.calendar)
    assert [item for plan in opens for item in plan['entry_signals']] == result['signals']
    assert [item for plan in closes for item in plan['exit_decisions']] == result['exit_decisions']
    if scenario == 'normal':
        assert result['fills']
        assert any(plan['holdings'] for plan in closes)


def test_top_three_ties_existing_position_late_input_and_no_hazard_replacement():
    b = bundle()
    account = PortfolioLedger(10000)
    row = first_rows(b).iloc[0].to_dict()
    symbols = ['600004.SH', '600003.SH', '600002.SH', '600001.SH', '600000.SH']
    rows = pd.DataFrame([{**row, 'symbol': symbol, 'value': -.1} for symbol in symbols])
    rows.loc[rows.symbol == '600003.SH', 'effective_available_at'] = at(20220803)
    fill, reason = account.apply_fill(Fill('F1', 'O1', CONTRACT['signal_version'], '600000.SH',
        Side.BUY, 100, 10, at(20220801)))
    assert fill is not None, reason
    lot = next(iter(account.lots.values()))
    lot.entry_session, lot.entry_session_index = 20220801, 0
    plan = preview(b, account, rows, hazards={'600001.SH': [20220804]})
    assert [item['symbol'] for item in plan['ranking']['top3']] == ['600001.SH', '600002.SH', '600004.SH']
    assert [item['symbol'] for item in plan['entry_signals']] == ['600002.SH', '600004.SH']
    assert plan['entry_checks'][0]['reason'] == 'ENTRY_REJECT_CORPORATE_ACTION_UNSUPPORTED'
    assert plan['entry_signals'][0]['generated_at'] == at(20220802)


def test_missing_data_is_not_empty_signal_and_window_end_has_no_fake_fill():
    b = bundle()
    account = PortfolioLedger(10000)
    missing = preview(b, account, None)
    empty = preview(b, account, first_rows(b).iloc[:0])
    assert missing['status'] == 'NOT_READY'
    assert missing['reason'] == 'FACTOR_SLICE_MISSING'
    assert empty['status'] == 'NO_SIGNAL'
    rows = b.ready_factors.loc[b.ready_factors.timestamp == 20220809]
    last = preview(b, account, rows, day=20220810)
    assert not last['entry_signals']
    assert {item['reason'] for item in last['entry_checks']} == {'END_OF_TRAIN_NO_COMPLETE_CLOSURE_PATH'}


def test_exit_is_lot_specific_pending_and_preview_does_not_mutate_account():
    b = bundle()
    account = PortfolioLedger(10000)
    for number, day in enumerate([20220801, 20220802]):
        fill, reason = account.apply_fill(Fill(f'F{number}', f'O{number}', CONTRACT['signal_version'],
            '600000.SH', Side.BUY, 100, 10, at(day)))
        assert fill is not None, reason
    lots = list(account.lots.values())
    for lot in lots:
        lot.entry_session = int(lot.buy_time.strftime('%Y%m%d'))
        lot.entry_session_index = b.calendar.index(lot.entry_session)
    before = account_identity(account)
    plan = preview(b, account, None, day=20220804, close=True)
    assert [item['lot_id'] for item in plan['exit_decisions']] == [lots[0].lot_id]
    assert [item['action'] for item in plan['holdings']] == ['EXIT', 'HOLD']
    assert plan['earliest_exit_execution'] == str(at(20220805))
    assert account_identity(account) == before
    lots[0].exit_state, lots[0].exit_reason = 'PARTIALLY_FILLED', 'EXIT_PENDING_RETRY'
    fill, reason = account.apply_fill(Fill('SELL1', 'SELL_ORDER1', CONTRACT['signal_version'],
        '600000.SH', Side.SELL, 50, 10, at(20220805)), lot_id=lots[0].lot_id)
    assert fill is not None, reason
    before = account_identity(account)
    partial = preview(b, account, None, day=20220805)
    assert partial['holdings'][0]['action'] == 'SELL_PENDING'
    assert partial['holdings'][0]['quantity'] == 50
    assert partial['holdings'][1]['quantity'] == 100
    assert account_identity(account) == before
    pending = preview(b, account, None, day=20220810, close=True)
    assert pending['exit_decisions'][0]['state'] == 'SELL_PENDING'
    assert pending['status'] == 'NOT_READY'
    assert pending['earliest_exit_execution'] is None
    blocked = preview(b, account, None, day=20220804, close=True,
        unsupported={lots[0].lot_id: {'reason': 'SYNTHETIC_UNSUPPORTED'}})
    assert not blocked['exit_decisions']
    assert blocked['holdings'][0]['action'] == 'BLOCKED'


@pytest.mark.parametrize('column,value', [
    ('effective_available_at', pd.NaT), ('effective_available_at', '2022-08-02 09:30'),
    ('value', float('-inf')), ('value', float('nan')), ('timestamp', 20220802),
    ('signal_version', 'OTHER_FACTOR'),
])
def test_invalid_slice_fails_closed(column, value):
    b = bundle()
    rows = first_rows(b)
    rows[column] = value
    with pytest.raises(ValueError, match='FIXED_'):
        preview(b, PortfolioLedger(10000), rows)


def test_duplicate_and_contract_changes_are_rejected():
    b = bundle()
    rows = first_rows(b)
    with pytest.raises(ValueError, match='ROW_IDENTITY'):
        preview(b, PortfolioLedger(10000), pd.concat([rows, rows.iloc[:1]]))
    with pytest.raises(ValueError, match='CONTRACT_MISMATCH'):
        preview(b, PortfolioLedger(10000), rows, contract_identity='wrong')
    with pytest.raises(ValueError, match='CONTRACT_UNSUPPORTED'):
        FixedAccountRules({**CONTRACT, 'holding_sessions': 4})


@pytest.mark.parametrize('options,code', [
    ({'plan_at': pd.NaT}, 'TIME_AWARE'),
    ({'plan_at': '2022-08-02 09:30'}, 'TIME_AWARE'),
    ({'plan_at': at(20220802) + pd.Timedelta(minutes=1)}, 'PHASE_TIME'),
    ({'account_stage': 'BEFORE_PENDING_ORDERS'}, 'ACCOUNT_STAGE'),
    ({'input_identity': ''}, 'INPUT_IDENTITY'),
])
def test_invalid_phase_and_identity(options, code):
    b = bundle()
    with pytest.raises(ValueError, match=code):
        preview(b, PortfolioLedger(10000), first_rows(b), **options)


def test_plan_identity_binds_account_inputs_and_source_without_io_mutation(monkeypatch):
    import socket
    import subprocess
    b = bundle()
    rows = first_rows(b)
    account = PortfolioLedger(10000)
    before = account_identity(account)
    original_open = Path.open

    def read_only(path, mode='r', *args, **kwargs):
        assert not any(flag in mode for flag in ('w', 'a', '+', 'x'))
        assert path.resolve().is_relative_to(Path(__file__).resolve().parents[2] / 'src')
        return original_open(path, mode, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError('preview cannot open a network connection or start a process')

    monkeypatch.setattr(Path, 'open', read_only)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    plan = preview(b, account, rows)
    assert plan == preview(b, account, rows)
    assert account_identity(account) == before
    assert plan['usage_qualification'] == 'NOT_FOR_QUALIFICATION'
    assert 'research_factory/fixed_account_rules.py' in plan['source_identity']
    account.cash = 9999
    assert plan['plan_id'] != preview(b, account, rows)['plan_id']
    changed = rows.copy()
    changed.loc[changed.index[0], 'value'] = -.2
    assert plan['plan_id'] != preview(b, PortfolioLedger(10000), changed)['plan_id']
    assert plan['plan_id'] != preview(b, PortfolioLedger(10000), rows, input_identity='NEW')['plan_id']


@pytest.mark.parametrize('cash,reserved', [(float('nan'), 0), (10000, float('inf')), (100, 101)])
def test_invalid_account_is_not_hidden_by_preview(cash, reserved):
    b = bundle()
    ledger = PortfolioLedger(10000)
    ledger.cash, ledger.reserved_cash = cash, reserved
    with pytest.raises(ValueError, match='ACCOUNT_INVALID'):
        preview(b, ledger, first_rows(b))


def test_known_lot_calendar_and_strategy_identity_are_required():
    b = bundle()
    ledger = PortfolioLedger(10000)
    ledger.apply_fill(Fill('F1', 'O1', 'WRONG_STRATEGY', '600000.SH', Side.BUY, 100, 10, at(20220801)))
    with pytest.raises(ValueError, match='LOT_IDENTITY'):
        preview(b, ledger, first_rows(b))
    lot = next(iter(ledger.lots.values()))
    lot.strategy_id = CONTRACT['signal_version']
    with pytest.raises(ValueError, match='LOT_IDENTITY'):
        preview(b, ledger, first_rows(b))


def test_execution_code_freeze_includes_shared_rules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    import execute_baostock_account_v1
    import run_degraded_account_v2
    for entry in [execute_baostock_account_v1, run_degraded_account_v2]:
        assert str(Path('src/chanlun_trader/research_factory/fixed_account_rules.py')) in entry.code_identity()
