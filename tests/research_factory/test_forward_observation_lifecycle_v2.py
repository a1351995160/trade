"""预登记观察阈值、退出与重放；只使用合成行情与原 Paper 服务。"""
import pytest

from chanlun_trader.research_factory.forward_paper_v1 import ForwardPaperSessionV1
from test_forward_paper_v1 import paper_case, paper_source, snapshot, ingest


def policy(**updates):
    return {'version': 'PAPER_OBSERVATION_POLICY_V1', 'min_complete_days': 1,
            'review_after': 2, 'max_drawdown_bps': 100, **updates}


def observed_case(tmp_path, paper_source, **updates):
    _, store, clock, kwargs, _ = paper_case(tmp_path, paper_source)
    session = ForwardPaperSessionV1.create(tmp_path / 'observed-paper', **kwargs,
                                          observation_policy=policy(**updates))
    return session, store, clock, kwargs


def test_drawdown_blocks_buys_preserves_exit_and_recovers_identically(tmp_path, paper_source):
    session, store, clock, _ = observed_case(tmp_path, paper_source)
    ingest(session, store, snapshot(store, clock, 'CLOSE', 20240801, 13.95, 14.1))
    opened = ingest(session, store, snapshot(store, clock, 'OPEN', 20240802, 14.1, 14.2))
    assert opened['state']['economic']['trades']
    value = snapshot(store, clock, 'CLOSE', 20240802, 14.1, 12.8)
    stopped = ingest(session, store, value)
    assert stopped['status'] == 'RISK_EXIT_ONLY'
    assert stopped['observation']['buy_blocked']
    assert stopped['observation']['minimum_sample_reached']
    assert not stopped['observation']['observation_qualified']
    assert stopped['real_observation_days'] == 0
    assert stopped['next_plan']['intents']
    assert all(item['side'] == 'SELL' for item in stopped['next_plan']['intents'])
    session = ForwardPaperSessionV1(session.root, clock=lambda: clock[0])
    assert ingest(session, store, value) == stopped
    exited = ingest(session, store, snapshot(store, clock, 'OPEN', 20240805, 12.8, 12.9))
    assert any(trade['side'] == 'SELL' for trade in exited['state']['economic']['trades'])
    assert all(position['quantity'] == 0 for position in exited['state']['economic']['positions'].values())
    recovered = ingest(session, store, snapshot(store, clock, 'CLOSE', 20240805, 12.8, 14.3))
    assert recovered['observation']['buy_blocked']  # 价格反弹不重置风险锁。
    assert not any(intent['side'] == 'BUY' for intent in recovered['next_plan']['intents'])
    replay = session._recover(session.header(), session._records(session.header()))
    assert replay.state() == recovered['state']


def test_frozen_review_day_stops_new_exposure_without_awarding_qualification(tmp_path, paper_source):
    session, store, clock, _ = observed_case(tmp_path, paper_source, review_after=1, max_drawdown_bps=5000)
    first = ingest(session, store, snapshot(store, clock, 'CLOSE', 20240801, 13.95, 14.1))
    assert first['observation']['completed_days'] == 0
    assert first['status'] == 'WAITING_DATA'
    ingest(session, store, snapshot(store, clock, 'OPEN', 20240802, 14.1, 14.2))
    value = snapshot(store, clock, 'CLOSE', 20240802, 14.1, 14.2)
    result = ingest(session, store, value)
    assert result['status'] == 'REVIEW_DUE'
    assert result['observation']['completed_days'] == 1
    assert result['observation']['review_due'] and result['observation']['buy_blocked']
    assert not result['observation']['observation_qualified']
    assert ingest(session, store, value)['observation']['completed_days'] == 1


@pytest.mark.parametrize('changes', [{'min_complete_days': 0}, {'review_after': 3}, {'review_after': 4},
                                    {'max_drawdown_bps': True}, {'version': 'UNKNOWN'}])
def test_invalid_or_out_of_window_observation_policy_rejected(tmp_path, paper_source, changes):
    with pytest.raises(ValueError, match='OBSERVATION_POLICY_INVALID'):
        observed_case(tmp_path, paper_source, **changes)
