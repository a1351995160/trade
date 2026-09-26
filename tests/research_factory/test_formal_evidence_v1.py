"""只用模拟采集器验证正式证据重放，不读取外部市场数据。"""
import json

import pytest

from chanlun_trader.research_factory import formal_evidence_v1 as evidence
from chanlun_trader.research_factory import forward_snapshot_v1 as snapshot


DAYS = [20260102, 20260105, 20260106]
SYMBOL = '000001.SZ'


def payload(day):
    return {'bars':[{'symbol':SYMBOL,'date':day,'open':10.,'high':10.,'low':10.,'close':10.,
                    'prev_close':10.,'volume':1000.,'amount':100000.,'price_basis':'RAW_CLOSE','amount_unit':'CNY'}],
            'turn':[{'symbol':SYMBOL,'date':day,'turn':1.,'tradestatus':1}],
            'states':[{'symbol':SYMBOL,'date':day,'listed':True,'delisted':False,'is_st':False,
                       'board':'MAIN','suspended':False}],
            'corporate_actions':[],'corporate_actions_complete':True}


def synthetic(tmp_path):
    store = snapshot.SnapshotStoreV1(tmp_path/'snapshots')
    ids = [store.record_synthetic(phase='CLOSE',market_date=day,payload=payload(day),
           received_at=f'{str(day)[:4]}-{str(day)[4:6]}-{str(day)[6:]}T15:30:00+08:00')['snapshot_id'] for day in DAYS]
    opens = [store.record_synthetic(phase='OPEN',market_date=day,payload=payload(day),
             received_at=f'{str(day)[:4]}-{str(day)[4:6]}-{str(day)[6:]}T09:31:00+08:00')['snapshot_id'] for day in DAYS[1:]]
    calendar = evidence.CalendarEvidenceStoreV1(tmp_path/'calendar').record_synthetic(
        start=DAYS[0],end=DAYS[-1],dates=DAYS,received_at='2026-01-06T16:00:00+08:00')
    return dict(snapshot_root=store.root,snapshot_ids=ids,calendar_root=tmp_path/'calendar',
        calendar_id=calendar['calendar_id'],symbols=[SYMBOL],not_before=DAYS[0],open_snapshot_ids=opens,
        frozen_at='2026-01-01T15:30:00+08:00',warmup_sessions=1,account_sessions=2,profile='SYNTHETIC')


def test_synthetic_bundle_never_becomes_real_or_qualified(tmp_path):
    args = synthetic(tmp_path)
    result = evidence.build_confirmation_bundle(**args)
    assert result['window']['account_start'] == DAYS[1]
    assert result['bundle']['daily'].adjustflag.tolist() == ['3']*3
    assert result['bundle']['turn'].volume.tolist() == [1000.]*3
    assert result['evidence']['independent_window'] is False
    assert result['evidence']['strategy_qualified'] is False
    assert len(result['bundle']['states']) == 2
    assert all('09:31' in stamp for stamp in result['bundle']['states'].available_at)
    with pytest.raises(ValueError,match='PROFILE'):
        evidence.build_confirmation_bundle(**{**args,'profile':'REAL_OBSERVED'})


@pytest.mark.parametrize('phase', ['CLOSE', 'OPEN'])
def test_unexplained_previous_close_is_rejected(tmp_path, phase):
    args = synthetic(tmp_path/'original')
    store = snapshot.SnapshotStoreV1(tmp_path/'changed')
    ids, opens = [], []
    for item_phase, days, destination in [('CLOSE',DAYS,ids),('OPEN',DAYS[1:],opens)]:
        for day in days:
            data = payload(day)
            if day == DAYS[1] and phase == item_phase:
                data['bars'][0]['prev_close'] = 9.5
            hour = '15:30' if item_phase == 'CLOSE' else '09:31'
            destination.append(store.record_synthetic(phase=item_phase,market_date=day,payload=data,
                received_at=f'{str(day)[:4]}-{str(day)[4:6]}-{str(day)[6:]}T{hour}:00+08:00')['snapshot_id'])
    with pytest.raises(ValueError,match='UNEXPLAINED_PRICE_REFERENCE'):
        evidence.build_confirmation_bundle(**{**args,'snapshot_root':store.root,
            'snapshot_ids':ids,'open_snapshot_ids':opens})


def test_duplicate_missing_revised_and_unknown_history_rejected(tmp_path):
    args = synthetic(tmp_path)
    with pytest.raises(ValueError,match='COUNT'):
        evidence.build_confirmation_bundle(**{**args,'snapshot_ids':args['snapshot_ids'][:-1]})
    with pytest.raises(ValueError,match='REORDERED'):
        evidence.build_confirmation_bundle(**{**args,'snapshot_ids':list(reversed(args['snapshot_ids']))})
    with pytest.raises(ValueError,match='FUTURE_FROZEN'):
        evidence.build_confirmation_bundle(**{**args,'frozen_at':'2026-01-02T10:00:00+08:00'})
    with pytest.raises(ValueError,match='OPEN_SNAPSHOT_COUNT'):
        evidence.build_confirmation_bundle(**{**args,'open_snapshot_ids':[]})
    snapshot.SnapshotStoreV1(args['snapshot_root']).record_synthetic(phase='CLOSE',market_date=DAYS[0],
        payload=payload(DAYS[0]),received_at='2026-01-02T16:00:00+08:00')
    with pytest.raises(ValueError,match='REVISED_CAPTURE'):
        evidence.build_confirmation_bundle(**args)


def test_calendar_tamper_and_skip_start_are_rejected(tmp_path):
    args = synthetic(tmp_path)
    other = evidence.CalendarEvidenceStoreV1(args['calendar_root']).record_synthetic(start=20260101,
        end=DAYS[-1],dates=DAYS,received_at='2026-01-06T16:00:00+08:00')
    with pytest.raises(ValueError,match='COVERAGE'):
        evidence.build_confirmation_bundle(**{**args,'calendar_id':other['calendar_id']})
    path = args['calendar_root']/(args['calendar_id']+'.json')
    value = json.loads(path.read_text(encoding='utf-8'))
    value['calendar'][0] = 20260101
    path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ValueError):
        evidence.CalendarEvidenceStoreV1(args['calendar_root']).load(args['calendar_id'])


def test_unresolved_action_is_never_certified(tmp_path):
    args = synthetic(tmp_path)
    # 专用目录中的原采集不修改，另建完整不合格输入集。
    store = snapshot.SnapshotStoreV1(tmp_path/'unresolved')
    ids = []
    opens = []
    for day in DAYS[1:]:
        opens.append(store.record_synthetic(phase='OPEN',market_date=day,payload=payload(day),
            received_at=f'{str(day)[:4]}-{str(day)[4:6]}-{str(day)[6:]}T09:31:00+08:00')['snapshot_id'])
    for day in DAYS:
        row = payload(day)
        row['corporate_actions_complete'] = False
        row['corporate_actions'] = [{'symbol':SYMBOL,'type':'UNRESOLVED_TDX_ACTION'}]
        ids.append(store.record_synthetic(phase='CLOSE',market_date=day,payload=row,
            received_at=f'{str(day)[:4]}-{str(day)[4:6]}-{str(day)[6:]}T15:30:00+08:00')['snapshot_id'])
    with pytest.raises(ValueError,match='CORPORATE_ACTION'):
        evidence.build_confirmation_bundle(**{**args,'snapshot_root':store.root,'snapshot_ids':ids,'open_snapshot_ids':opens})


def test_real_capture_is_replayed_from_uncached_provider_responses(tmp_path, monkeypatch):
    from chanlun_trader.data.tdx import tq_client
    current = [DAYS[0]]
    hour = ['15:30']
    requests = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['use_cache'] is False
        def request(self, method, params, *, use_cache):
            assert use_cache is False
            requests.append(method)
            day = current[0]
            if method == 'get_match_stkinfo': return {'Value':[]}
            if method == 'get_trading_calendar':
                return {'Value':[d for d in DAYS if int(params['start_time'])<=d<=int(params['end_time'])]}
            if method == 'get_market_snapshot': return {SYMBOL:{'LastClose':10.,'Now':10.,'Volume':10.,'Amount':100000.}}
            if method == 'get_stock_info':
                return {SYMBOL:dict(DelayMin=0,HSStockKind='1',J_start='19910101',IsQuitGP=0,IsSTGP=0,TodayDRFlag=0)}
            if method == 'get_more_info': return {SYMBOL:dict(HqDate=day,TPFlag=0,fHSL=1.)}
            if method == 'get_market_data':
                return {SYMBOL:dict(Date=[day],Open=[10.],High=[10.],Low=[10.],Close=[10.],Volume=[1000.],Amount=[10.])}
            raise AssertionError(method)
    def now():
        return snapshot._stamp(f'{str(current[0])[:4]}-{str(current[0])[4:6]}-{str(current[0])[6:]}T{hour[0]}:00+08:00')
    monkeypatch.setattr(tq_client,'TQClient',Client)
    monkeypatch.setattr(snapshot,'assert_tdx_ready',lambda:None)
    monkeypatch.setattr(evidence,'assert_tdx_ready',lambda:None)
    monkeypatch.setattr(snapshot,'_now',now)
    monkeypatch.setattr(evidence,'_now',now)
    store = snapshot.SnapshotStoreV1(tmp_path/'snapshots')
    ids = []
    opens = []
    for day in DAYS:
        current[0] = day
        if day in DAYS[1:]:
            hour[0] = '09:31'
            opens.append(store.capture_tdx(phase='OPEN',symbols=[SYMBOL])['snapshot_id'])
        hour[0] = '15:30'
        ids.append(store.capture_tdx(phase='CLOSE',symbols=[SYMBOL])['snapshot_id'])
    calendar = evidence.CalendarEvidenceStoreV1(tmp_path/'calendar').capture_tdx(DAYS[0],DAYS[-1])
    built = evidence.build_confirmation_bundle(snapshot_root=store.root,snapshot_ids=ids,
        calendar_root=tmp_path/'calendar',calendar_id=calendar['calendar_id'],symbols=[SYMBOL],
        not_before=DAYS[0],frozen_at='2026-01-01T15:30:00+08:00',warmup_sessions=1,account_sessions=2,
        open_snapshot_ids=opens)
    assert built['evidence']['independent_window'] is True
    assert built['bundle']['states'].board.unique().tolist() == ['MAIN']
    assert requests.count('get_trading_calendar') == 6


def test_real_profile_relabel_without_capture_source_rejected(tmp_path):
    args = synthetic(tmp_path)
    value = snapshot.SnapshotStoreV1(args['snapshot_root']).load(args['snapshot_ids'][0])
    value.update(profile='REAL_OBSERVED',provider='TDX_TQ_LOCAL_UNCACHED_V1')
    with pytest.raises(ValueError,match='REAL_CAPTURE_SOURCE'):
        evidence._verify_real_close(value,[SYMBOL])
