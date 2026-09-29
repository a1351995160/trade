"""真实Paper接口上的合成确定性测试，不伪造真实资格。"""
import hashlib

import pandas as pd
import pytest

from test_forward_paper_v1 import paper_source, paper_case, snapshot, ingest
from chanlun_trader.research_factory.trusted_daily_plan_v1 import trusted_daily_plan


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def ready(tmp_path, paper_source, members=2):
    session, store, clock, kwargs, archive = paper_case(tmp_path, paper_source, members=members)
    ingest(session, store, snapshot(store, clock, 'CLOSE', 20240801, 13.95, 14.10))
    return session, store, clock, archive


def test_shared_cash_symbol_limit_and_readonly(tmp_path, paper_source):
    session, _, _, _ = ready(tmp_path, paper_source)
    before = hashes(session.root)
    result = trusted_daily_plan(session)
    assert result['status'] == 'PLANNED'
    assert result['portfolio_qualified'] is False
    assert result['real_execution_authorized'] is False
    assert result['valid_date'] == 20240802
    assert 0 < result['estimated_buy_cost'] <= result['available_cash']
    assert all(row['quantity'] % 100 == 0 for row in result['intents'])
    for symbol in session.header()['policy']['symbols']:
        gross = sum(row['quantity'] * row['reference_price'] for row in result['intents'] if row['symbol'] == symbol)
        assert gross <= 50000
    assert hashes(session.root) == before
    assert trusted_daily_plan(session)['plan_identity'] == result['plan_identity']


@pytest.mark.parametrize('reason', ['expired', 'member_revoked', 'session_revoked'])
def test_unusable_plan_never_suggests_trades(tmp_path, paper_source, reason):
    session, _, clock, archive = ready(tmp_path, paper_source)
    if reason == 'expired':
        clock[0] = pd.Timestamp('2024-08-02T09:36:00+08:00')
    elif reason == 'member_revoked':
        for key in session.header()['strategies']:
            archive.revoke(key, 'qualification withdrawn')
    else:
        session.revoke('authorization withdrawn')
    before = hashes(session.root)
    result = trusted_daily_plan(session)
    assert result['intents'] == []
    assert result['status'] in {'EXPIRED', 'NO_ADMITTED_STRATEGIES', 'REVOKED'}
    assert hashes(session.root) == before


def test_no_close_data_waits_without_mutation(tmp_path, paper_source):
    session, _, _, _, _ = paper_case(tmp_path, paper_source)
    before = hashes(session.root)
    result = trusted_daily_plan(session)
    assert result['status'] == 'WAITING_DATA'
    assert result['intents'] == []
    assert hashes(session.root) == before


def test_open_stage_does_not_reuse_previous_plan(tmp_path, paper_source):
    session, store, clock, _ = ready(tmp_path, paper_source)
    ingest(session, store, snapshot(store, clock, 'OPEN', 20240802, 14.10, 14.15))
    before = hashes(session.root)
    assert trusted_daily_plan(session)['status'] == 'WAITING_DATA'
    assert hashes(session.root) == before


def test_formal_members_do_not_grant_portfolio_qualification(tmp_path, paper_source, monkeypatch):
    session, _, _, _ = ready(tmp_path, paper_source)
    header = session.header()
    records = session._records(header)
    engine = session._recover(header, records)
    admissions = session._admissions(header)
    for value in admissions.values():
        value.update(allowed=True, strategy_qualified=True, review_hash='a' * 64)
    header['purpose'] = 'FORMAL_OBSERVATION'
    monkeypatch.setattr(session, 'header', lambda: header)
    monkeypatch.setattr(session, '_records', lambda *_args, **_kwargs: records)
    monkeypatch.setattr(session, '_recover', lambda *_: engine)
    monkeypatch.setattr(session, '_admissions', lambda *_: admissions)
    result = trusted_daily_plan(session)
    assert result['status'] == 'WAITING_QUALIFICATION'
    assert result['portfolio_qualified'] is False
    assert result['intents'] == []
