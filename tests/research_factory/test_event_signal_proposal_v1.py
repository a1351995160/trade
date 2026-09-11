"""只用手算数据验证提案，不触及真实输入与runner。"""
import pytest

from chanlun_trader.research_factory.event_signal_proposal_v1 import (
    five_session_return, universe_preflight,
)


def fixture():
    days = [20220805, 20220808, 20220809, 20220810, 20220811, 20220812]
    actions = [{'session': d, 'kind': 'VERIFIED_NONE', 'cash_per_old_share': 0,
                'new_shares_per_old_share': 0, 'terms_known_by_decision': True} for d in days[1:]]
    return days, actions


def test_cash_bonus_neutralizes_mechanical_drop():
    days, actions = fixture()
    actions[0].update(kind='CASH_AND_BONUS', cash_per_old_share=2, new_shares_per_old_share=1)
    result = five_session_return(days, [10, 4, 4, 4, 4, 4], actions, coverage_complete=True)
    assert result['value'] == pytest.approx(0)


def test_no_events_telescopes_and_real_price_move_remains():
    days, actions = fixture()
    assert five_session_return(days, [10, 9, 11, 10, 9, 8], actions,
                               coverage_complete=True)['value'] == pytest.approx(-.2)
    actions[0].update(kind='CASH_AND_BONUS', cash_per_old_share=2, new_shares_per_old_share=1)
    assert five_session_return(days, [10, 4, 4, 4, 4, 5], actions,
                               coverage_complete=True)['value'] == pytest.approx(.25)


@pytest.mark.parametrize('condition,reason', [
    ('coverage', 'ACTION_COVERAGE_UNPROVEN'), ('late', 'TERMS_NOT_KNOWN_BY_DECISION'),
    ('rights', 'UNSUPPORTED_ACTION'), ('gap', 'MISSING_DEPENDENCY'),
    ('wrong_date', 'ACTION_SESSION_CONFLICT'), ('nan', 'INVALID_PRICE'),
])
def test_undefined_is_not_zero(condition, reason):
    days, actions = fixture(); closes = [10] * 6
    if condition == 'late': actions[0]['terms_known_by_decision'] = False
    if condition == 'rights': actions[0]['kind'] = 'RIGHTS_ISSUE'
    if condition == 'gap': closes.pop()
    if condition == 'wrong_date': actions[0]['session'] = 20220806
    if condition == 'nan': closes[0] = float('nan')
    assert five_session_return(days, closes, actions, coverage_complete=condition != 'coverage') == {
        'value': None, 'reason': reason}


def test_missing_eligible_member_is_not_silently_removed():
    rows = {'A': {'state': 'ELIGIBLE', 'signal_inputs_complete': True},
            'B': {'state': 'ELIGIBLE', 'signal_inputs_complete': False},
            'C': {'state': 'KNOWN_INELIGIBLE', 'signal_inputs_complete': False}}
    result = universe_preflight(['A', 'B', 'C'], rows)
    assert not result['ready'] and result['eligible_missing_inputs'] == ['B']
    assert result['historical_members'] == ['A', 'B', 'C']
    rows['B']['signal_inputs_complete'] = True
    assert universe_preflight(['A', 'B', 'C'], rows)['ready']


def test_unknown_state_cannot_be_inferred_from_available_price():
    assert universe_preflight(['A'], {'A': {'state': 'UNKNOWN', 'signal_inputs_complete': True}})['unknown_state'] == ['A']
