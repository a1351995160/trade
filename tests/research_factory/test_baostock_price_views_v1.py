import pandas as pd
import pytest

from chanlun_trader.research_factory.baostock_price_views_v1 import prepare_symbol

DAYS = [20220722,20220725,20220726,20220727,20220728,20220729,20220801,20220802]


def rows():
    raw=[]; hfq=[]
    for i, d in enumerate(DAYS):
        price=10 if i<3 else 5
        raw.append(dict(code='sh.600000', date=str(pd.Timestamp(str(d)).date()),adjustflag='3',
            open=price,high=price,low=price,close=price,preclose=price,volume=100000,amount=price*100000,
            tradestatus='1',isST='0'))
        hfq.append(dict(code='sh.600000',date=raw[-1]['date'],adjustflag='1',close=70))
    return raw,hfq


def test_split_does_not_create_reversal_and_store_is_raw():
    raw,hfq=rows();v=prepare_symbol('600000.SH',raw,hfq,DAYS)
    assert v.features.iloc[5].value==0
    assert v.features.iloc[5].effective_available_at==pd.Timestamp('2022-08-01 09:30',tz='Asia/Shanghai')
    assert v.execution_store().get_daily_bar('600000.SH',20220729)['open']==5
    assert not v.execution_store().daily_hfq
    assert pd.isna(v.features.iloc[-1].value)
    assert raw[0]['open']==10


@pytest.mark.parametrize('side,field,value', [('raw','adjustflag','2'),('hfq','adjustflag','3'),('raw','code','sz.000001')])
def test_wrong_mode_or_identity_rejected(side,field,value):
    raw,hfq=rows();(raw if side=='raw' else hfq)[0][field]=value
    with pytest.raises(ValueError,match='CONFLICT'):prepare_symbol('600000.SH',raw,hfq,DAYS)


def test_calendar_gap_and_suspension_remain_uncomputable():
    raw,hfq=rows();del hfq[2]
    v=prepare_symbol('600000.SH',raw,hfq,DAYS)
    assert v.features.value.isna().all() and len(v.diagnostics)==len(DAYS)
    raw,hfq=rows();raw[2].update(tradestatus='0',volume='',amount='')
    v=prepare_symbol('600000.SH',raw,hfq,DAYS)
    assert pd.isna(v.raw.iloc[2].volume) and v.features.value.isna().all()


def test_scaled_hfq_and_future_price_do_not_change_past_signal():
    raw,hfq=rows();hfq[5]['close']=63
    v=prepare_symbol('600000.SH',raw,hfq,DAYS)
    assert v.features.iloc[5].value==pytest.approx(-.1)
    for r in hfq:r['close']*=11
    hfq[-1]['close']=999999
    assert prepare_symbol('600000.SH',raw,hfq,DAYS).features.iloc[5].value==pytest.approx(-.1)


def test_duplicate_or_outside_window_rejected():
    raw,hfq=rows()
    with pytest.raises(ValueError,match='DUPLICATE'):prepare_symbol('600000.SH',raw+raw[:1],hfq,DAYS)
    with pytest.raises(ValueError,match='TRAIN'):prepare_symbol('600000.SH',raw,hfq,DAYS+[20240801])


def test_later_source_time_overrides_model_without_becoming_historical_pit():
    raw,hfq=rows();raw[3]['source_published_at']='2022-08-02T11:00:00+08:00'
    v=prepare_symbol('600000.SH',raw,hfq,DAYS)
    assert v.features.iloc[5].effective_available_at==pd.Timestamp('2022-08-02 11:00',tz='Asia/Shanghai')
    assert v.raw.iloc[3].source_published_at==raw[3]['source_published_at']
    raw[3]['source_published_at']='2022-08-02 11:00'
    with pytest.raises(ValueError,match='TIMEZONE'):prepare_symbol('600000.SH',raw,hfq,DAYS)


def test_adapter_features_feed_original_engine_while_fills_use_raw():
    from test_degraded_execution_v2 import degraded
    from chanlun_trader.research_factory.baostock_account_v1 import run_baostock_account, CONTRACT
    from chanlun_trader.research_factory.common import stable_hash
    b=degraded(); pre=DAYS[:6]; frames=[]; actual=[]
    for symbol, group in b.daily.groupby('symbol'):
        raw=[];hfq=[];code='sh.'+symbol[:6]
        source=group.sort_values('date').to_dict('records')
        source=[dict(source[0],date=d,close=10.5,open=10.5,high=10.6,low=10.4,prev_close=10.5) for d in pre]+source
        for row in source:
            raw.append(dict(code=code,date=str(pd.Timestamp(str(row['date'])).date()),adjustflag='3',
                **{k:row[k] for k in ['open','high','low','close','volume','amount']},
                preclose=row['prev_close'],tradestatus='1',isST='0'))
            hfq.append(dict(code=code,date=raw[-1]['date'],adjustflag='1',close=row['close']*7))
        views=prepare_symbol(symbol,raw,hfq,pre+b.calendar)
        frames.append(views.features.dropna(subset=['value']))
        actual.append(views.raw[views.raw.date.isin(b.calendar)])
    b.daily=pd.concat(actual,ignore_index=True);b.ready_factors=pd.concat(frames,ignore_index=True)
    b.contract_identity=stable_hash(CONTRACT)
    result=run_baostock_account(b,('SYNTHETIC',False))
    buys=[x for x in result['fills'] if x['side']=='BUY']
    assert buys and buys[0]['price']<11
    assert result['status']=='COMPLETE'
    assert result['contract']==CONTRACT
    with pytest.raises(PermissionError):run_baostock_account(b,('ACTUAL',False))
    from chanlun_trader.research_factory.degraded_execution_v2 import CONTRACT as OLD
    with pytest.raises(PermissionError,match='RECEIPT'):
        run_baostock_account(b,('ACTUAL',False),lambda:{'plan':{'input_identity':b.input_identity,'contracts':{'old':OLD}}})
    b.daily['adjustflag']='2'
    with pytest.raises(ValueError,match='RAW'):run_baostock_account(b,('SYNTHETIC',False))
