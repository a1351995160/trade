"""方法研发凭证不兑换正式资格；执行范围不能默认为旧百万账户。"""
from copy import deepcopy
import pytest
from chanlun_trader.research_factory import formal_rule_adapter_v3 as adapter
from chanlun_trader.research_factory.common import stable_hash
from scripts.study_formal_account_method_v3 import SPEC, inputs, proposal


def test_scope_binds_cash_pool_and_position_limits():
    scope = adapter.execution_scope(SPEC)
    assert scope['initial_cash'] == 50000 and len(scope['symbols']) == 3
    for field, value in [('initial_cash', 1000000), ('max_positions', 4), ('max_symbol_exposure_bps', 0)]:
        bad = {**SPEC, field: value}
        with pytest.raises(ValueError):
            adapter.execution_scope(bad)
    with pytest.raises(ValueError):
        adapter.execution_scope(SPEC, ['000001.SZ'])


def test_no_caller_assertion_can_approve_method_or_synthetic_publication():
    for profile in ['REAL_OBSERVED', 'SYNTHETIC', 'SYNTHETIC_METHOD_DEVELOPMENT']:
        scope = {**SPEC, 'applicable': True, 'method_approved': True, 'profile': profile}
        resolution = adapter.method_resolver(scope)
        assert not resolution['applicable']
        assert resolution['scope_hash'] == stable_hash(scope)
        with pytest.raises(PermissionError, match='PUBLICATION_NOT_APPROVED'):
            adapter.require_publication(scope, profile=profile)


def test_v3_prepare_uses_fifty_thousand_and_exact_pool():
    window, _ = inputs(0)
    plan = adapter.prepare_rule(proposal(), strategy_id='method', window=window,
        costs='BASE', scope=SPEC, execution_profile='HISTORICAL_MODELED')
    assert plan['backend']['initial_cash'] == 50000
    assert plan['backend']['max_positions'] == 3
    assert plan['backend']['window']['symbols'] == sorted(SPEC['symbols'])


def test_freeze_is_exclusive(tmp_path):
    from scripts.study_formal_account_method_v3 import write_new
    p = tmp_path/'protocol.json'
    write_new(p, {'frozen': True})
    with pytest.raises(FileExistsError):
        write_new(p, {'frozen': False})


def test_frozen_study_is_exact_unsupported_counterexample():
    import json
    import numpy as np
    from chanlun_trader.research_factory.formal_statistics_v2 import family_test
    from chanlun_trader.research_factory.method_applicability_v1 import reference_t7_upper_tail
    root = adapter.STUDY_PATH.parent
    report = json.loads(adapter.STUDY_PATH.read_text(encoding='utf8'))
    assert stable_hash(report) == adapter.STUDY_HASH
    assert adapter.method_resolver(SPEC)['evidence_hash'] == adapter.STUDY_HASH
    paths = {}
    for sign in (-1, 0, 1):
        r = json.loads((root/f'account_{sign}.json').read_text(encoding='utf8'))
        cash = json.loads((root/f'cash_{sign}.json').read_text(encoding='utf8'))
        assert stable_hash(r) == report['atoms'][str(sign)]['result_hash']
        assert stable_hash(cash) == report['atoms'][str(sign)]['cash_hash']
        assert r['reconciliation'] == {'passed': True, 'days': 504}
        assert not r['strategy_qualified'] and not cash['strategy_qualified']
        assert not cash['fills']
        equity = np.array([50000.]+[x['equity'] for x in r['daily_accounts']])
        paths[sign] = equity[1:]/equity[:-1]-1
        test = family_test({f'member_{i}': paths[sign] for i in range(5)}, alpha=.0125)
        assert test == report['atoms'][str(sign)]['test']
        assert test['raw_p']['member_0'] == pytest.approx(reference_t7_upper_tail(test['statistics']['member_0']), abs=1e-12)
    assert ((paths[-1]+paths[1])/2).mean() < 0
    assert report['exact_family_error'] == .5
    assert report['exact_power'] == 1
    assert report['decision'] == 'UNSUPPORTED'


def test_missing_or_tampered_study_never_claims_evidence(tmp_path, monkeypatch):
    path = tmp_path/'report.json'
    monkeypatch.setattr(adapter, 'STUDY_PATH', path)
    assert adapter.method_resolver(SPEC)['evidence_hash'] is None
    path.write_text('{"applicable": true}', encoding='utf8')
    assert adapter.method_resolver(SPEC)['evidence_hash'] is None
    assert not adapter.method_resolver(SPEC)['applicable']
