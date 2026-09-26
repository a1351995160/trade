"""正式家族路由合成状态测试；账户与独立数据在原集成套件另验。"""
from copy import deepcopy
import math

import pytest

from test_formal_assessment_v1 import harness
from chanlun_trader.research_factory import research_screening_v1 as screening
from chanlun_trader.research_factory import formal_assessment_v1 as formal


def test_only_screened_member_executes_but_full_family_remains(harness, monkeypatch):
    service, family, register, run, calls, _ = harness
    ids = family(members=2)
    origin = service.archive.load(ids[0])['origin']['source_root']
    from pathlib import Path
    report = {'selected': ['CANDIDATE_001'], 'reports': {'CANDIDATE_001': {'passed': True},
                                                       'CANDIDATE_002': {'passed': False}}}
    monkeypatch.setattr(screening, 'validated_screening', lambda root, archives: deepcopy(report))
    status = register(ids, screening_root=Path(origin).parent/'screening')
    plan = service.plan(status['batch_id'])
    assert plan['account_budget'] == 3 and len(plan['family']) == 2
    result = run(status['batch_id'])
    assert calls == ['BENCHMARK_BASE', 'CANDIDATE_001_BASE', 'CANDIDATE_001_STRESS']
    assert result['decisions']['CANDIDATE_002']['reason_codes'] == ['HISTORICAL_SCREEN_FAILED']
    assert set(result['statistics']['supported']) == {'CANDIDATE_001', 'CANDIDATE_002'}
    assert not result['strategy_qualified']
    assert service.report(status['batch_id']) == result
    report['selected'].append('CANDIDATE_002')
    with pytest.raises(ValueError, match='SCREENING_CHANGED'):
        service.status(status['batch_id'])


def test_all_failed_does_not_consume_formal_slot(harness, monkeypatch):
    service, family, register, _, _, _ = harness
    ids = family()
    from pathlib import Path
    root = Path(service.archive.load(ids[0])['origin']['source_root']).parent/'screening'
    monkeypatch.setattr(screening, 'validated_screening', lambda *args: {'selected': []})
    with pytest.raises(ValueError, match='NO_SCREENED_CANDIDATES'):
        register(ids, screening_root=root)
    assert service._plans() == []


def test_unselected_member_remains_untestable_in_statistics():
    dates = list(range(504))
    rows = [{'date': d, 'net_return': .001} for d in dates]
    candidate = {'daily_returns': rows, 'status': 'RECONCILED_DIAGNOSTIC'}
    values = formal.FormalAssessmentServiceV1._excess(
        {'family': {'a': {'frozen': True}, 'b': {'frozen': True}}, 'screening': {'selected': ['a']}},
        {'BENCHMARK_BASE': candidate, 'a_BASE': candidate, 'a_STRESS': candidate})
    assert values['a'] == [0.] * 504 and all(math.isnan(x) for x in values['b'])
    result = formal.statistics_v2.family_test(values, alpha=.0125)
    assert result['adjusted_p']['b'] is None and not result['supported']['b']
    assert set(result['adjusted_p']) == {'a', 'b'}


def test_diagnosis_family_cannot_bypass_screening(harness, monkeypatch):
    service, family, register, _, _, _ = harness
    ids = family()
    original = service.archive.load
    def load(key):
        result = original(key)
        result['evidence']['session']['input_manifest'] = {'diagnosis_config_hash': 'SYNTHETIC_BINDING'}
        return result
    monkeypatch.setattr(service.archive, 'load', load)
    with pytest.raises(ValueError, match='DIAGNOSIS_SCREENING_REQUIRED'):
        register(ids)
    assert service._plans() == []
