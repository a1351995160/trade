"""探索档案完整性与观察准入；仅使用合成行情、模型和真实账户代码。"""
from copy import deepcopy
import json

import pytest

from test_bounded_real_research_loop_v1 import FakeInvoker, make_session
from chanlun_trader.research_factory import bounded_research_v1 as research
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.strategy_qualification_v1 import BoundedStrategyArchiveV1


@pytest.fixture(scope='module')
def completed(tmp_path_factory):
    session, loader, _ = make_session(tmp_path_factory.mktemp('qualification'), attempts=1)
    session.run(loader=loader, invoker=FakeInvoker())
    return session


@pytest.fixture
def service(tmp_path):
    return BoundedStrategyArchiveV1(tmp_path / 'archives')


def test_freeze_copies_verified_evidence_and_is_idempotent(completed, service, monkeypatch):
    archive = service.freeze(completed.root, 'CANDIDATE_001')
    assert service.freeze(completed.root, 'CANDIDATE_001') == archive
    monkeypatch.setattr(research, 'source_identity', lambda: 'NEW_APPLICATION_VERSION')
    # 历史冻结来源不因应用升级失效，也不需要重新访问源目录或重跑账户。
    monkeypatch.setattr(research.BoundedResearchSessionV1, 'scope', lambda *a, **k: pytest.fail('不得重读来源'))
    assert service.load(archive['strategy_id']) == archive
    assert archive['source_profile'] == 'SYNTHETIC'
    assert archive['qualification'] == 'NOT_ASSESSED'
    assert archive['origin']['scope_id'] == archive['evidence']['session']['scope_id']


def test_engineering_observation_never_means_formal_qualification(completed, service):
    archive = service.freeze(completed.root, 'CANDIDATE_001')
    sid = archive['strategy_id']
    allowed = service.admission(sid, purpose='ENGINEERING_OBSERVATION')
    assert allowed['allowed'] and not allowed['strategy_qualified']
    assert allowed['source_profile'] == 'SYNTHETIC'
    formal = service.admission(sid, purpose='FORMAL_OBSERVATION')
    assert not formal['allowed'] and not formal['strategy_qualified']
    assert set(formal['reason_codes']) >= {'INDEPENDENT_CONFIRMATION_REQUIRED', 'FORMAL_STATISTICAL_METHOD_NOT_APPROVED',
                                         'SYNTHETIC_SOURCE_NOT_QUALIFIED'}
    report = service.review(sid)
    assert report['qualification'] == 'BLOCKED'
    assert report['metrics']['account_days'] > 0
    assert set(report['assessment']) == {'economic', 'sample', 'execution', 'cost', 'independence', 'publication'}
    with pytest.raises(TypeError):
        service.admission(sid, purpose='FORMAL_OBSERVATION', qualified=True)
    with pytest.raises(TypeError):
        service.admission(sid, purpose='FORMAL_OBSERVATION', gates={'all': True})


def test_revocation_is_permanent_and_missing_tail_is_blocked(completed, service):
    sid = service.freeze(completed.root, 'CANDIDATE_001')['strategy_id']
    first = service.revoke(sid, '暂停观察')
    assert service.revoke(sid, '不能恢复') == first
    assert service.admission(sid, purpose='ENGINEERING_OBSERVATION')['reason_codes'] == ['STRATEGY_REVOKED']
    service._path(sid, 'REVOKED.json').unlink()
    with pytest.raises(ValueError, match='COMMITTED_HEAD_CONFLICT'):
        service.admission(sid, purpose='ENGINEERING_OBSERVATION')
    with pytest.raises(ValueError, match='COMMITTED_HEAD_CONFLICT'):
        service.revoke(sid, '不能用重试修复被删撤销')


def test_missing_head_and_archive_tampering_fail_closed(completed, service):
    sid = service.freeze(completed.root, 'CANDIDATE_001')['strategy_id']
    service._path(sid, 'HEAD.json').unlink()
    with pytest.raises(FileNotFoundError):
        service.admission(sid, purpose='ENGINEERING_OBSERVATION')
    path = service._path(sid, 'ARCHIVE.json')
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['qualification'] = 'RESEARCH_PASSED'
    path.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError, match='CORRUPT'):
        service.load(sid)


@pytest.mark.parametrize('defect', ['attempt', 'settlement', 'start', 'receipt', 'trial', 'budget', 'diagnostic', 'plan'])
def test_rehashed_but_inconsistent_evidence_rejected(completed, service, defect):
    archive = service.freeze(completed.root, 'CANDIDATE_001')
    evidence = deepcopy(archive['evidence'])
    if defect == 'attempt':
        evidence['attempt']['reservation'] = 'TRIAL-OTHER'
    elif defect == 'settlement':
        evidence['settlement']['result_sha256'] = 'OTHER'
    elif defect == 'start':
        evidence['start']['kind'] = 'REFERENCE'
    elif defect == 'receipt':
        evidence['receipt']['source']['scope_id'] = 'OTHER'
        evidence['receipt']['receipt_id'] = stable_hash({k: v for k, v in evidence['receipt'].items() if k != 'receipt_id'})
    elif defect == 'trial':
        evidence['trials']['events'] = [e for e in evidence['trials']['events']
                                        if e['trial_id'] != 'CANDIDATE_001' or e['event_type'] != 'REGISTERED_BEFORE_PERFORMANCE']
        evidence['trials']['event_count'] = len(evidence['trials']['events'])
        evidence['trials']['registry_hash'] = stable_hash(evidence['trials']['events'])
    elif defect == 'budget':
        evidence['budget']['settled_reservations'][evidence['attempt']['reservation']] = 'RELEASED'
    elif defect == 'diagnostic':
        evidence['diagnostic']['metrics']['net_return'] = 9.9
        evidence['diagnostic']['diagnostic_hash'] = stable_hash({k: v for k, v in evidence['diagnostic'].items() if k != 'diagnostic_hash'})
    elif defect == 'plan':
        evidence['result']['strategy_plan']['plan_id'] = 'OTHER'
    with pytest.raises(ValueError, match='STRATEGY_ARCHIVE_'):
        service._validate(evidence, archive['origin'])


def test_current_rule_incompatibility_blocks_engineering_observation(completed, service, monkeypatch):
    from chanlun_trader.research_factory.bounded_candidate_v1 import BoundedVoteStrategy
    sid = service.freeze(completed.root, 'CANDIDATE_001')['strategy_id']
    def incompatible(self):
        raise ValueError('RULE_CHANGED')
    monkeypatch.setattr(BoundedVoteStrategy, 'validate', incompatible)
    result = service.admission(sid, purpose='ENGINEERING_OBSERVATION')
    assert not result['allowed'] and result['reason_codes'] == ['CURRENT_RULE_NOT_EXECUTABLE']


def test_failed_account_cannot_be_promoted_by_completion_file(completed, service):
    archive = service.freeze(completed.root, 'CANDIDATE_001')
    evidence = deepcopy(archive['evidence'])
    evidence['settlement']['completed'] = False
    evidence['settlement']['error'] = 'ACCOUNT_UNRECONCILED'
    with pytest.raises(ValueError, match='SETTLEMENT_CONFLICT'):
        service._validate(evidence, archive['origin'])


def test_missing_source_evidence_blocks_freeze(completed, service, monkeypatch):
    from chanlun_trader.research_factory import strategy_qualification_v1 as qualification
    original = qualification._read
    def missing_settlement(path):
        if path.name == 'ATTEMPT.json':
            raise FileNotFoundError(path)
        return original(path)
    monkeypatch.setattr(qualification, '_read', missing_settlement)
    with pytest.raises(FileNotFoundError):
        service.freeze(completed.root, 'CANDIDATE_001')
    assert not list(service.root.rglob('ARCHIVE.json'))


def test_strategy_id_is_scope_bound_and_paths_cannot_escape(completed, service):
    archive = service.freeze(completed.root, 'CANDIDATE_001')
    assert archive['strategy_id'] == 'BS_' + stable_hash({'scope_id': archive['origin']['scope_id'], 'candidate_id': 'CANDIDATE_001'})
    assert archive['strategy_id'] != 'BS_' + stable_hash({'scope_id': 'ANOTHER_SCOPE', 'candidate_id': 'CANDIDATE_001'})
    with pytest.raises(ValueError, match='ID_INVALID'):
        service.load('../ARCHIVE')
    with pytest.raises(ValueError, match='CANDIDATE_REQUIRED'):
        service.freeze(completed.root, 'REFERENCE')
