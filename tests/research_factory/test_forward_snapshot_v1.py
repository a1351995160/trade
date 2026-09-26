"""实际到达证据与合成输入边界；不依赖运行中的供应商客户端。"""
from copy import deepcopy
import json

import pytest

from chanlun_trader.research_factory import forward_snapshot_v1 as snapshots


def payload():
    return {'bars':[{'symbol':'600000.SH','date':20260928,'open':10.,'high':11.,'low':9.,
        'close':10.5,'prev_close':10.,'volume':10000.,'amount':100000.}],
        'turn':[{'symbol':'600000.SH','date':20260928,'turn':1.,'tradestatus':1}],
        'states':[{'symbol':'600000.SH','date':20260928,'listed':True,'delisted':False,
            'is_st':False,'board':'MAIN','suspended':False}],
        'corporate_actions':[],'corporate_actions_complete':True}


def record(store, data=None):
    return store.record_synthetic(phase='CLOSE',market_date=20260928,payload=data or payload(),
        received_at='2026-09-28T15:02:00+08:00')


def test_snapshot_is_immutable_idempotent_and_synthetic(tmp_path):
    store = snapshots.SnapshotStoreV1(tmp_path)
    item = record(store)
    assert item['profile'] == 'SYNTHETIC'
    assert store.load(item['snapshot_id']) == item == record(store)
    assert len(list(tmp_path.glob('SNAP_*.json'))) == 1
    changed = payload(); changed['turn'][0]['turn'] = 2.
    revision = record(store, changed)
    assert revision['snapshot_id'] != item['snapshot_id']


@pytest.mark.parametrize('fault', ['date','coverage','number','state','duplicate','actions'])
def test_invalid_snapshot_rejected_before_commit(tmp_path, fault):
    data = deepcopy(payload())
    if fault == 'date': data['bars'][0]['date'] = 20260925
    if fault == 'coverage': data['turn'][0]['symbol'] = '000001.SZ'
    if fault == 'number': data['bars'][0]['volume'] = float('nan')
    if fault == 'state': data['states'][0]['listed'] = None
    if fault == 'duplicate': data['bars'].append(deepcopy(data['bars'][0]))
    if fault == 'actions': del data['corporate_actions_complete']
    with pytest.raises(ValueError): record(snapshots.SnapshotStoreV1(tmp_path), data)
    assert not list(tmp_path.glob('SNAP_*.json'))


def test_receipt_time_and_path_cannot_be_forged(tmp_path):
    store = snapshots.SnapshotStoreV1(tmp_path)
    with pytest.raises(ValueError,match='SAME_DAY'):
        store.record_synthetic(phase='CLOSE',market_date=20260928,payload=payload(),received_at='2026-09-29T15:00:00+08:00')
    with pytest.raises(ValueError,match='AWARE'):
        store.record_synthetic(phase='CLOSE',market_date=20260928,payload=payload(),received_at='2026-09-28T15:00:00')
    with pytest.raises(ValueError,match='ID_INVALID'): store.load('../outside')
    item = record(store)
    path = tmp_path/(item['snapshot_id']+'.json')
    raw = json.loads(path.read_text(encoding='utf-8')); raw['profile']='REAL_OBSERVED'
    path.write_text(json.dumps(raw),encoding='utf-8')
    with pytest.raises(ValueError,match='CORRUPT'): store.load(item['snapshot_id'])


def test_real_capture_cannot_accept_offline_payload_or_fake_receipt_time(tmp_path,monkeypatch):
    store = snapshots.SnapshotStoreV1(tmp_path)
    with pytest.raises(TypeError): store.capture_tdx(phase='CLOSE',symbols=['600000.SH'],received_at='2026-09-28')
    monkeypatch.setattr(snapshots,'assert_tdx_ready',lambda: (_ for _ in ()).throw(RuntimeError('TDX_CLIENT_NOT_RUNNING')))
    with pytest.raises(RuntimeError,match='CLIENT_NOT_RUNNING'): store.capture_tdx(phase='CLOSE',symbols=['600000.SH'])
    assert not list(tmp_path.glob('SNAP_*.json'))


def provider(monkeypatch, *, fault=None, phase='CLOSE'):
    from chanlun_trader.data.tdx import tq_client
    calls = []
    monkeypatch.setattr(snapshots, 'assert_tdx_ready', lambda: None)
    stamp = snapshots._stamp('2026-09-28T15:02:00+08:00' if phase == 'CLOSE' else '2026-09-28T09:32:00+08:00')
    monkeypatch.setattr(snapshots, '_now', lambda: stamp)
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['use_cache'] is False
        def request(self, method, params, *, use_cache):
            assert use_cache is False
            calls.append((method, params))
            if method == 'get_match_stkinfo': return {'600519.SH':'茅台'}
            if method == 'get_trading_calendar': return [] if fault == 'holiday' else ['20260928']
            if method == 'get_market_snapshot':
                return {'Now':10.5,'LastClose':10.,'Volume':100.,'Amount':100000.}
            if method == 'get_stock_info':
                return {'HSStockKind':1,'J_start':20000101,'IsQuitGP':0,'IsSTGP':0,
                        'TodayDRFlag':1 if fault == 'action' else 0, 'DelayMin':15 if fault == 'delay' else 0}
            if method == 'get_more_info':
                return {'HqDate':20260925 if fault == 'stale' else 20260928,'TPFlag':0,'fHSL':1.25}
            assert method == 'get_market_data'
            assert params['dividend_type'] == 'none' and params['fill_data'] is False
            return {'600000.SH':{'Date':['20260928'],'Open':[10.],'High':[11.],'Low':[9.],
                                'Close':[10.5],'Volume':[10000.],'Amount':[10.]}}
    monkeypatch.setattr(tq_client, 'TQClient', Client)
    return calls


@pytest.mark.parametrize('phase', ['OPEN','CLOSE'])
def test_provider_capture_preserves_receipt_raw_responses_and_units(tmp_path,monkeypatch,phase):
    calls = provider(monkeypatch, phase=phase)
    store = snapshots.SnapshotStoreV1(tmp_path)
    item = store.capture_tdx(phase=phase,symbols=['600000.SH'])
    assert store.load(item['snapshot_id']) == item
    assert item['profile'] == 'REAL_OBSERVED'
    bar = item['payload']['bars'][0]
    assert bar['volume'] == 10000. and bar['amount'] == 100000.
    assert item['payload']['turn'][0]['turn'] == 1.25
    assert calls[0][0] == 'get_match_stkinfo'
    assert len(item['source_responses']) == len(calls)
    if phase == 'OPEN':
        assert bar['open'] == 10.5 and bar['price_basis'] == 'OBSERVED_NOW'
        assert 'get_market_data' not in [call[0] for call in calls]
    else:
        assert bar['open'] == 10. and bar['amount_unit'] == 'CNY'


@pytest.mark.parametrize('fault,code', [('holiday','NOT_EXCHANGE'),('stale','STALE'),('delay','DELAYED')])
def test_provider_refuses_stale_delayed_and_nontrading_input(tmp_path,monkeypatch,fault,code):
    provider(monkeypatch,fault=fault)
    with pytest.raises(ValueError,match=code):
        snapshots.SnapshotStoreV1(tmp_path).capture_tdx(phase='CLOSE',symbols=['600000.SH'])
    assert not list(tmp_path.glob('SNAP_*.json'))


def test_provider_does_not_invent_action_details(tmp_path,monkeypatch):
    provider(monkeypatch,fault='action')
    item = snapshots.SnapshotStoreV1(tmp_path).capture_tdx(phase='CLOSE',symbols=['600000.SH'])
    assert item['payload']['corporate_actions_complete'] is False
    assert item['payload']['corporate_actions'][0]['type'] == 'UNRESOLVED_TDX_ACTION'
