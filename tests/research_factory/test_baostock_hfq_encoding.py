"""提供者参数映射的独立回归，不从实现复制错误标签。"""
import pytest
from test_baostock_price_views_v1 import rows, DAYS
from chanlun_trader.research_factory.baostock_price_views_v1 import prepare_symbol


def test_provider_post_adjusted_flag_one_is_accepted():
    raw, hfq = rows()
    for row in hfq:
        row['adjustflag'] = '1'
    assert prepare_symbol('600000.SH', raw, hfq, DAYS).features.iloc[5].value == 0


def test_provider_pre_adjusted_flag_two_is_not_hfq():
    raw, hfq = rows()
    for row in hfq:
        row['adjustflag'] = '2'
    with pytest.raises(ValueError, match='PRICE_MODE_CONFLICT'):
        prepare_symbol('600000.SH', raw, hfq, DAYS)


def test_certified_listing_identity_is_accepted_without_rewriting_provider_flag():
    raw,hfq = rows()
    for i,r in enumerate(raw):
        r.update(open=10,high=10,low=10,close=10,preclose=10)
        hfq[i].update(close=10,adjustflag='3' if i<2 else '1')
    states = [{'trade_date':20220721,'listed':False},{'trade_date':DAYS[0],'listed':True}]
    with pytest.raises(ValueError,match='PRICE_MODE_CONFLICT'):
        prepare_symbol('600000.SH',raw,hfq,DAYS)
    result = prepare_symbol('600000.SH',raw,hfq,DAYS,state_rows=states)
    assert result.features.iloc[5].value == 0
    assert hfq[0]['adjustflag'] == '3'
