from copy import deepcopy
from decimal import Decimal
import pandas as pd
import pytest
from chanlun_trader.research_factory.weekly_alias_v1 import adapt,canonical_pools,FACTOR


def sample():
    before=['2024-10-09']+pd.bdate_range('2024-10-10',periods=84).strftime('%Y-%m-%d').tolist()+['2025-02-14']
    dates=before+['2025-02-17','2025-02-18']
    raw=[dict(date=d,code='sz.302132',open='10',high='10',low='10',close='10',preclose='10',
        volume='10000',amount='100000',adjustflag='3',tradestatus='1',isST='0') for d in dates]
    hfq=[dict(date=d,code='sz.302132',close='10' if d<='2025-02-17' else str(10*FACTOR),
        adjustflag='3' if d<'2025-02-17' else '1') for d in dates]
    old_raw=[{**r,'code':'sz.300114'} for r in raw[:-2]]
    old_hfq=[dict(date=d,code='sz.300114',close=str(10*FACTOR),adjustflag='1') for d in before]
    return raw,hfq,old_raw,old_hfq


def test_original_failure_and_green_derived_pair():
    from chanlun_trader.research_factory.baostock_input_v1 import verify_pair
    from chanlun_trader.research_factory.baostock_price_views_v1 import prepare_symbol
    from chanlun_trader.research_factory.weekly_window_v1 import contract
    args=sample();original=deepcopy(args)
    days=[int(r['date'].replace('-','')) for r in args[0]]
    old=verify_pair('302132.SZ',{'error_code':'0','rows':args[0]}, {'error_code':'0','rows':args[1]},days,[])
    assert not old['passed'] and len(old['failures'])==86
    raw,hfq,evidence=adapt(*args)
    assert args==original
    assert verify_pair('302132.SZ',{'error_code':'0','rows':raw},{'error_code':'0','rows':hfq},days,[])['passed']
    assert len(raw)==len(hfq)==88
    assert raw[-2]['close']=='10' and hfq[-2]['close']==str(10*FACTOR)
    assert hfq[-1]==args[1][-1]
    assert evidence['not_for_qualification'] and evidence['raw_values_changed'] is False
    views=prepare_symbol('302132.SZ',raw,hfq,days,window_contract=contract())
    assert views.features.value.dropna().eq(0).all()
    assert views.execution_store().get_daily_bar('302132.SZ',20250217)['close']==10


@pytest.mark.parametrize('change',['raw','prior','following','duplicate'])
def test_conflicting_evidence_rejected(change):
    args=sample()
    if change=='raw':args[2][0]['volume']='9'
    elif change=='prior':args[3][0]['close']='1'
    elif change=='following':args[1][-1]['close']='1'
    else:args[0].append(dict(args[0][0]))
    with pytest.raises(ValueError):adapt(*args)


def test_asof_pool_no_duplicate_identity_or_missing_fabrication():
    pools={20250214:{'sz.300114':'1','sz.302132':'0'},20250217:{'sz.300114':'0','sz.302132':'1'}}
    result=canonical_pools(pools)
    assert result[20250214]['sz.302132']=='1'
    assert result[20250217]==pools[20250217]
    assert pools[20250214]['sz.302132']=='0'
    with pytest.raises(ValueError):canonical_pools({20250214:{'sz.302132':'1'}})
