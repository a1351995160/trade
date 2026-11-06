"""隔离父事件提交的最后时间边界；完整合法来源链另由V2集成夹具核验。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory import governance_source_continuation_v2 as continuation
from chanlun_trader.research_factory.budget import BudgetLedgerMismatchError
from chanlun_trader.research_factory.run_budget import AutonomousRunBudgetV2


@pytest.fixture
def submission(tmp_path, monkeypatch):
    issued = datetime(2026, 11, 6, tzinfo=timezone.utc)
    deadline = issued + timedelta(seconds=259200)
    clock = {'now': issued + timedelta(seconds=10)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = clock['now']
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(continuation, 'datetime', Clock)
    budget = AutonomousRunBudgetV2(run_id='artificial_submission', objective_id='original_objective',
        path=tmp_path/'run_budget.json', max_batches=1, max_total_predictive_trials=1,
        max_trials_per_batch=1, max_hypotheses_per_batch=1, max_candidates_per_batch=1)
    authorization = {'root': str(tmp_path), 'authorization_id': 'original_authorization',
        'objective_id': 'original_objective'}
    # 仅隔离完整候选重放之后的提交边界；不以本存根证明合法来源／Owner链。
    monkeypatch.setattr(budget, 'campaign_view', lambda: {'authorization': authorization})
    proof = {'manifest_ref': {'path': str(tmp_path/'MANIFEST.json'), 'sha256': 'a'*64},
        'continuation_id': 'b'*64}
    fresh = {'calls': 0}

    def validate_return(*, for_dispatch=False):
        assert for_dispatch is False
        fresh['calls'] += 1
        if fresh.get('expire_during_read'):
            clock['now'] = deadline
        if fresh.get('owner_revoked'):
            raise PermissionError('OWNER_APPROVAL_NOT_REGISTERED')

    context = SimpleNamespace(summary={'issued_at': issued.isoformat(), 'ledger': {'line_count': 0},
        'operation_ids': {'BASE': 'original_base', 'STRESS': 'original_stress'}},
        execution_expires_at=deadline.isoformat(), proof=lambda: proof, validate_return=validate_return)
    monkeypatch.setattr(continuation, 'resolve_continuation', lambda *args: context)
    payload = {'proof': proof, 'installed_at': clock['now'].isoformat()}
    return SimpleNamespace(budget=budget, root=tmp_path, authorization=authorization, issued=issued,
        deadline=deadline, clock=clock, payload=payload, fresh=fresh, context=context)


@pytest.mark.parametrize('case', ['before_issuance', 'future_installation', 'expired', 'crossed_during_fresh', 'owner_revoked'])
def test_new_parent_event_rejects_late_or_future_authority_without_writing(submission, case):
    value = submission
    if case == 'before_issuance':
        value.clock['now'] = value.issued-timedelta(seconds=1)
        value.payload['installed_at'] = value.issued.isoformat()
    elif case == 'future_installation':
        value.payload['installed_at'] = (value.clock['now']+timedelta(seconds=1)).isoformat()
    elif case == 'expired':
        value.clock['now'] = value.deadline
    elif case == 'crossed_during_fresh':
        value.fresh['expire_during_read'] = True
    else:
        value.fresh['owner_revoked'] = True
    before = {p: p.read_bytes() for p in value.root.rglob('*') if p.is_file()}
    with pytest.raises(BudgetLedgerMismatchError, match='GOVERNANCE_CONTINUATION'):
        value.budget.campaign_event(continuation.EVENT, value.payload)
    assert value.budget._events == []
    assert {p: p.read_bytes() for p in value.root.rglob('*') if p.is_file()} == before


def test_legal_new_parent_event_rechecks_owner_then_appends_once(submission):
    value = submission
    value.budget.campaign_event(continuation.EVENT, value.payload)
    assert value.fresh['calls'] == 1
    assert len(value.budget._events) == 1
    assert value.budget._events[0]['event_type'] == continuation.EVENT
    assert value.budget._events[0]['payload'] == value.payload


def test_historical_parent_replay_remains_readable_after_expiry(submission, monkeypatch):
    value = submission
    value.clock['now'] = value.deadline+timedelta(seconds=1)
    monkeypatch.setattr(continuation, 'validate_parent_boundary', lambda *args: None)
    operations = {'original_base': {}, 'original_stress': {}}
    continuation.apply_parent_continuation(value.authorization, operations, value.payload, False, event_index=0)
    assert all(row['governance_continuation']['proof'] == value.payload['proof'] for row in operations.values())
    assert value.budget._events == []
