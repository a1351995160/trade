"""固定组合观察：真实服务合成账户测试，不产生真实资格。"""
from copy import deepcopy
import pytest

from test_forward_paper_v1 import paper_case, paper_source, snapshot, ingest
from chanlun_trader.research_factory.portfolio_qualification_v1 import PortfolioQualificationV1, _account_risk


def review_policy():
    return {'min_complete_days': 3, 'max_drawdown_bps': 5000, 'max_total_cost_bps': 5000,
            'max_symbol_exposure_bps': 10000, 'min_trades_per_member': 1, 'min_net_return_bps': -10000}


def freeze(tmp_path, kwargs):
    return PortfolioQualificationV1.freeze(tmp_path / 'qualification', archive_root=kwargs['archive_root'],
        policy=kwargs['policy'], calendar=kwargs['calendar'], review_policy=review_policy(), profile='SYNTHETIC')


def test_empty_observation_is_waiting_and_not_qualified(tmp_path, paper_source):
    session, _, _, kwargs, _ = paper_case(tmp_path, paper_source, members=2)
    service = freeze(tmp_path, kwargs)
    assert service.review()['status'] == 'WAITING_OBSERVATION'
    service.bind_session(session.root)
    report = service.review()
    assert not report['portfolio_qualified']
    assert 'OBSERVATION_WINDOW_INCOMPLETE' in report['reason_codes']
    assert 'SYNTHETIC_OBSERVATION_NOT_QUALIFIED' in report['reason_codes']


def test_completed_combined_account_replayed_and_synthetic_never_qualified(tmp_path, paper_source):
    session, store, clock, kwargs, _ = paper_case(tmp_path, paper_source, members=2)
    from chanlun_trader.research_factory.forward_paper_v1 import ForwardPaperSessionV1
    # 两成员分别是MACD与RSI；单调预热会让RSI饱和于100而永不产生上涨信号。
    # 合成正向用例显式加入回撤再恢复，让两个成员确实交易；活动门槛保持不变。
    warmup = deepcopy(kwargs['warmup'])
    for symbol in kwargs['policy']['symbols']:
        rows = [row for row in warmup['bars'] if row['symbol'] == symbol]
        rows[-2].update(close=13.5, low=13.4)
        rows[-1]['prev_close'] = 13.5
    kwargs = {**kwargs, 'warmup': warmup}
    session = ForwardPaperSessionV1.create(tmp_path / 'active-members-paper', **kwargs)
    service = freeze(tmp_path, kwargs)
    service.bind_session(session.root)
    ingest(session, store, snapshot(store, clock, 'CLOSE', 20240801, 13.95, 14.1))
    previous = 14.1
    for day in kwargs['calendar'][1:]:
        ingest(session, store, snapshot(store, clock, 'OPEN', day, previous, previous + .1))
        ingest(session, store, snapshot(store, clock, 'CLOSE', day, previous, previous + .1))
        previous += .1
    report = service.review()
    assert report['observation_risk_pass']
    assert not report['portfolio_qualified']
    assert not report['statistical_effectiveness_proven']
    assert all(count >= 1 for count in report['metrics']['trades_per_member'].values())
    assert service.review() == report


def test_cannot_bind_after_observing_outcomes(tmp_path, paper_source):
    session, store, clock, kwargs, _ = paper_case(tmp_path, paper_source, members=2)
    service = freeze(tmp_path, kwargs)
    ingest(session, store, snapshot(store, clock, 'CLOSE', 20240801, 13.95, 14.1))
    with pytest.raises(ValueError, match='RETROSPECTIVE_BINDING'):
        service.bind_session(session.root)


def test_single_member_and_short_real_window_rejected(tmp_path, paper_source):
    _, _, _, kwargs, _ = paper_case(tmp_path, paper_source)
    with pytest.raises(ValueError, match='TWO_MEMBERS'):
        freeze(tmp_path, kwargs)


def test_combined_drawdown_and_same_symbol_exposure_not_member_average():
    frozen = {'policy': {'initial_cash': 1000}, 'members': {'A': {}, 'B': {}},
              'review_policy': {**review_policy(), 'max_drawdown_bps': 1000,
                                'max_symbol_exposure_bps': 5000}}
    record = {'snapshot': {'phase': 'CLOSE', 'payload': {'bars': [{'symbol': 'S', 'close': 10}]}},
        'state': {'equity': 800, 'invariant_errors': [], 'economic': {
            'positions': {'A': {'strategy_id': 'A', 'symbol': 'S', 'quantity': 30},
                          'B': {'strategy_id': 'B', 'symbol': 'S', 'quantity': 30}}, 'trades': []}}}
    metrics, reasons = _account_risk([record], frozen)
    assert metrics['max_symbol_exposure_bps'] == 7500
    assert 'COMBINED_DRAWDOWN_EXCEEDED' in reasons
    assert 'COMBINED_SYMBOL_EXPOSURE_EXCEEDED' in reasons
    assert 'MEMBER_ACTIVITY_EVIDENCE_INSUFFICIENT' in reasons


def test_incomplete_risk_evidence_rejected():
    frozen = {'policy': {'initial_cash': 1000}, 'members': {'A': {}, 'B': {}},
              'review_policy': review_policy()}
    record = {'snapshot': {'phase': 'CLOSE', 'payload': {'bars': []}},
        'state': {'equity': 1000, 'invariant_errors': [], 'economic': {
            'positions': {'A': {'strategy_id': 'A', 'symbol': 'S', 'quantity': 100}}, 'trades': []}}}
    with pytest.raises(ValueError, match='EXPOSURE_DATA_MISSING'):
        _account_risk([record], frozen)


def test_formal_paper_rechecks_combination_scope_and_current_review(monkeypatch):
    # 仅验证正式入口与评审服务的合同；此mock不构成真实正向资格。
    from chanlun_trader.research_factory.forward_paper_v1 import _portfolio_qualification
    policy = {'portfolio': {'purpose': 'ENGINEERING_OBSERVATION', 'members': ['A', 'B']}, 'initial_cash': 1000}
    frozen = {'policy': policy, 'members': {'A': {}, 'B': {}}, 'calendar': [20240801, 20240802], 'portfolio_id': 'P'}
    current = {'portfolio_qualified': True, 'company_actions': 'STOP_ON_ANY_OBSERVATION_ACTION'}
    monkeypatch.setattr(PortfolioQualificationV1, 'frozen', lambda self: deepcopy(frozen))
    monkeypatch.setattr(PortfolioQualificationV1, 'review', lambda self: deepcopy(current))
    formal_policy = deepcopy(policy)
    formal_policy['portfolio']['purpose'] = 'FORMAL_OBSERVATION'
    kwargs = {'policy': formal_policy, 'strategy_ids': ['A', 'B'], 'calendar': [20240805],
              'company_actions': 'STOP_ON_ANY_OBSERVATION_ACTION', 'expected_id': 'P'}
    assert _portfolio_qualification('.', **kwargs)[1]['portfolio_qualified']
    current['portfolio_qualified'] = False
    assert not _portfolio_qualification('.', **kwargs)[1]['portfolio_qualified']
    with pytest.raises(ValueError, match='SCOPE_CONFLICT'):
        _portfolio_qualification('.', **{**kwargs, 'calendar': [20240802]})
    with pytest.raises(ValueError, match='SCOPE_CONFLICT'):
        _portfolio_qualification('.', **{**kwargs, 'policy': {**formal_policy, 'initial_cash': 2000}})
