from test_baostock_price_views_v1 import rows, DAYS
from chanlun_trader.research_factory.baostock_input_v1 import verify_pair
from chanlun_trader.research_factory.baostock_input_v1 import verified_ipo_prefix


def check(raw, hfq, states=()):
    return verify_pair('600000.SH', {'error_code':'0','rows':raw},
        {'error_code':'0','rows':hfq}, DAYS, states)


def test_paired_inputs_and_suspension_blanks_are_preserved():
    raw, hfq = rows()
    raw[2].update(tradestatus='0',volume='',amount='')
    result = check(raw,hfq)
    assert result['passed'] and result['suspended_rows_preserved'] == 1
    assert raw[2]['volume'] == ''


def test_missing_day_wrong_price_mode_and_state_conflict_fail():
    raw, hfq = rows()
    assert not check(raw,hfq[1:])['passed']
    hfq[0]['adjustflag'] = '2'
    assert not check(raw,hfq)['passed']
    raw,hfq = rows()
    state = dict(trade_date=DAYS[0],listed=True,delisted=False,universe_member=True,
        eligibility_status='ELIGIBLE',st_status='ST',suspension_status='TRADING')
    assert check(raw,hfq,[state])['failures'][0]['reason'] == 'HISTORICAL_STATE_CONFLICT'


def test_unavailable_lifecycle_does_not_become_zero_or_removed_member():
    raw,hfq = rows()
    state = dict(trade_date=DAYS[0],listed=True,delisted=False,universe_member=True,
        eligibility_status='ELIGIBLE',st_status='NORMAL',suspension_status='TRADING')
    result = check(raw[1:],hfq[1:],[state])
    assert result['failures'][0]['reason'] == 'HISTORICAL_LIFECYCLE_PRICE_MISSING'
    assert result['historical_eligible_days'] == 1


def test_new_feasibility_does_not_read_old_candidate_paths():
    from test_degraded_execution_v2 import degraded
    from chanlun_trader.research_factory.degraded_input_v2 import check_feasibility
    bundle = degraded()
    bundle.coverage = []
    bundle.access = []
    result = check_feasibility(bundle, candidate_paths=[])
    assert result['candidate_paths'] == 0 and not result['passed']


def test_ipo_prefix_requires_independent_listing_and_exact_price_recurrence():
    raw = [{'code':'sz.001376','date':'2023-11-03','close':'10','preclose':'8'},
           {'code':'sz.001376','date':'2023-11-06','close':'9','preclose':'10'}]
    hfq = [dict(r,adjustflag='3') for r in raw]
    state = [{'trade_date':20231102,'listed':False},{'trade_date':20231103,'listed':True}]
    assert verified_ipo_prefix(raw,hfq,state) == ['2023-11-03','2023-11-06']
    assert all(r['adjustflag'] == '3' for r in hfq)
    assert verified_ipo_prefix(raw,hfq,state[1:]) == []
    raw[1]['preclose'] = '5'
    assert verified_ipo_prefix(raw,hfq,state) == []
    raw[1]['preclose'] = '10'
    hfq[1]['close'] = '18'
    assert verified_ipo_prefix(raw,hfq,state) == []
