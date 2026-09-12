"""新日线时间模型与未知状态边界；不读取真实研究数据。"""
import pandas as pd
import pytest

from chanlun_trader.data.tdx.execution_input import ExecutionTrainingAdapterV1,BoundedTQEvidenceProviderV1,classify_state,effective_time

def frame():
    return pd.DataFrame([dict(symbol='600000.SH',date=d,open=10,high=11,low=9,close=10,volume=100,amount=1000,available_at=None) for d in [20220801,20220802]])

def test_close_event_before_visibility_and_next_session_open():
    adapter=ExecutionTrainingAdapterV1(frame(),[20220801,20220802])
    assert adapter.observed_history('600000.SH','2022-08-01T15:00:00+08:00').empty
    assert len(adapter.observed_history('600000.SH','2022-08-01T15:00:00.000001+08:00'))==1
    assert str(adapter.next_open(20220801))=='2022-08-02 09:30:00+08:00'
    assert adapter.next_open(20220802) is None
    assert adapter.available_at('close','600000.SH',20220801) is None

def test_later_real_evidence_wins_and_future_is_cut():
    rows=frame(); rows.loc[0,'available_at']='2022-08-03T09:30:00+08:00'
    adapter=ExecutionTrainingAdapterV1(rows,[20220801,20220802])
    assert adapter.observed_history('600000.SH','2022-08-02T09:30:00+08:00').empty
    assert effective_time(20220801,'2022-08-04T10:00:00+08:00')==pd.Timestamp('2022-08-04T10:00:00+08:00')

def test_units_unknown_cannot_be_used_for_execution():
    adapter=ExecutionTrainingAdapterV1(frame(),[20220801,20220802])
    with pytest.raises(ValueError,match='UNITS'): adapter.get_bars('600000.SH')
    with pytest.raises(ValueError,match='AWARE'): adapter.observed_history('600000.SH','2022-08-01')

@pytest.mark.parametrize('st,susp,expected',[('NORMAL','TRADING','KNOWN_ELIGIBLE'),('ST','TRADING','KNOWN_ST'),('NORMAL','SUSPENDED','KNOWN_SUSPENDED'),('UNKNOWN','TRADING','UNKNOWN')])
def test_states_are_not_dropped_or_filled(st,susp,expected):
    assert classify_state(dict(universe_member=True,eligibility_status='ELIGIBLE',st_status=st,suspension_status=susp))==expected

def test_calendar_provider_fixed_parameters_and_no_cache():
    class Client:
        def request(self,method,params,**kwargs):
            assert method=='get_trading_calendar'
            assert params=={'market':'SH','start_time':'20220701','end_time':'20220801'}
            assert kwargs=={'use_cache':False,'retries':1}
            return {'Date':['20220729','20220801'],'ErrorId':'0'}
    assert BoundedTQEvidenceProviderV1(Client()).calendar(20220701,20220801)==[20220729,20220801]

def test_out_of_scope_rejected_before_call():
    class Client:
        def request(self,*args,**kwargs): pytest.fail('No out-of-scope request')
    with pytest.raises(ValueError,match='SCOPE'): BoundedTQEvidenceProviderV1(Client()).calendar(20220701,20240801)


def test_warmup_reader_never_reads_price_outside_six_sessions(tmp_path):
    from chanlun_trader.data.tdx.execution_input import read_warmup_day_window, _DAY_STRUCT
    sessions = [20220722,20220725,20220726,20220727,20220728,20220729]
    path = tmp_path/'test.day'
    path.write_bytes(b''.join(_DAY_STRUCT.pack(d,1000,1100,900,1000,100.0,10,b"0000")
                             for d in [20220721,*sessions,20220801,20250801]))
    rows,audit=read_warmup_day_window(path,sessions)
    assert rows.date.tolist()==sessions
    assert audit['price_byte_range_half_open']==[32,224]
    assert rows.close.tolist()==[10]*6
    path.write_bytes(_DAY_STRUCT.pack(20220721,1000,1100,900,1000,100.0,10,b"0000"))
    rows,audit=read_warmup_day_window(path,sessions)
    assert rows.empty and audit['price_bytes_read']==0


def test_unsafe_action_provider_refuses_before_transport():
    class Client:
        def request(self,*args,**kwargs): pytest.fail('Known unsafe provider must not receive request')
    with pytest.raises(PermissionError,match='WINDOW_NOT_ENFORCED'):
        BoundedTQEvidenceProviderV1(Client()).corporate_actions('000001.SZ',20220801,20240731)


def test_owner_action_export_keeps_unknown_evidence_and_rejects_scope():
    from chanlun_trader.data.tdx.execution_input import normalize_windowed_actions
    row=dict(event_id='synthetic-dividend',symbol='600000.SH',effective_date=20220802,
             event_type='CASH_DIVIDEND',terms={'cash_per_share':1},units='CNY_PER_SHARE',
             source='SYNTHETIC',source_published_at=None)
    assert normalize_windowed_actions([row],[20220802],{'600000.SH'})==[row]
    with pytest.raises(ValueError,match='SCOPE'):
        normalize_windowed_actions([row],[20220801],{'600000.SH'})
    with pytest.raises(ValueError,match='IDENTITY'):
        normalize_windowed_actions([row,row],[20220802],{'600000.SH'})


def test_cash_and_share_action_counterexamples_require_accounting():
    # 确定型算术反例，不把反例预期冒充引擎已生成的公司行动账务。
    shares,price=100,10
    assert shares*price==1000
    assert shares*(price-1)+shares*1==1000
    assert shares*(price-1)==900  # 忽略现金分红少记100元。
    assert (shares*2)*(price/2)==1000
    assert shares*(price/2)==500  # 忽略送转少记100股。
