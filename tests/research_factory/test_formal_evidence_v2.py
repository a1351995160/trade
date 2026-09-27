"""现金分红正式证据通路：全部来源是显式合成或模拟原始响应。"""
from copy import deepcopy
import hashlib
import json

import pytest

from chanlun_trader.engine.corporate_accounting_v1 import identity
from chanlun_trader.research_factory.formal_evidence_v1 import CalendarEvidenceStoreV1
from chanlun_trader.research_factory.formal_evidence_v2 import build_rule_confirmation_bundle, _verify_real_cash
from chanlun_trader.research_factory.forward_snapshot_v1 import SnapshotStoreV1
from test_formal_evidence_v1 import DAYS, SYMBOL, payload


def source(path, value):
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return {'source_ref': str(path.absolute()), 'source_sha256': hashlib.sha256(raw).hexdigest()}


def fixture(tmp_path, *, dividend=True, late=False, coverage=True):
    store = SnapshotStoreV1(tmp_path / 'snapshots')
    ids, opens = [], []
    event = {'event_id': 'CASH', 'symbol': SYMBOL, 'event_type': 'CASH_DIVIDEND',
        'record_date': DAYS[1], 'effective_date': DAYS[2], 'payment_date': DAYS[2],
        'source_published_at': '2026-01-02T13:00:00+08:00', 'source': 'SYNTHETIC_ANNOUNCEMENT',
        'units': 'CNY_PER_SHARE', 'terms': {'cash_per_share': .1,
            'tax_rule': {'kind': 'DEFERRED_INDIVIDUAL_2015_101', 'source': 'SYNTHETIC_TAX'}}}
    envelope = {'version': 'CorporateActionEnvelopeV1', 'revision': 1, 'mode': 'OBSERVED',
        'provider': 'SYNTHETIC_FIXTURE', 'received_at': '2026-01-06T09:00:00+08:00' if late
        else '2026-01-02T14:00:00+08:00', 'event': event}
    envelope.update(source(tmp_path / 'event.json', {key: envelope[key] for key in ('provider', 'received_at', 'event')}))
    for day in DAYS:
        for phase in (['CLOSE'] if day == DAYS[0] else ['OPEN', 'CLOSE']):
            stamp = f'{str(day)[:4]}-{str(day)[4:6]}-{str(day)[6:]}T' + ('09:31' if phase == 'OPEN' else '15:30') + ':00+08:00'
            data = payload(day)
            if dividend and day == DAYS[2]:
                data['bars'][0].update(open=9.9, high=9.9, low=9.9, close=9.9, prev_close=9.9)
            announce = day == (DAYS[2] if late else DAYS[0]) and phase == ('OPEN' if late else 'CLOSE')
            envelopes = [envelope] if dividend and announce else []
            data['corporate_actions'] = [e['event'] for e in envelopes]
            data['corporate_action_envelopes'] = envelopes
            if coverage:
                document = {'profile': 'SYNTHETIC', 'provider': 'SYNTHETIC_FIXTURE', 'received_at': stamp,
                    'market_date': day, 'symbols': [SYMBOL], 'complete': True,
                    'envelope_hashes': [identity(e) for e in envelopes]}
                data['corporate_action_coverage'] = {**document, **source(tmp_path / f'{day}_{phase}.json', document)}
            item = store.record_synthetic(phase=phase, market_date=day, payload=data, received_at=stamp)
            (ids if phase == 'CLOSE' else opens).append(item['snapshot_id'])
    calendar = CalendarEvidenceStoreV1(tmp_path / 'calendar').record_synthetic(start=DAYS[0], end=DAYS[-1],
        dates=DAYS, received_at='2026-01-06T16:00:00+08:00')
    return dict(snapshot_root=store.root, snapshot_ids=ids, open_snapshot_ids=opens,
        calendar_root=tmp_path / 'calendar', calendar_id=calendar['calendar_id'], symbols=[SYMBOL],
        not_before=DAYS[0], frozen_at='2026-01-01T15:30:00+08:00', warmup_sessions=1,
        account_sessions=2, profile='SYNTHETIC')


@pytest.mark.parametrize('dividend', [False, True])
def test_complete_source_coverage_constructs_bundle_without_granting_qualification(tmp_path, dividend):
    result = build_rule_confirmation_bundle(**fixture(tmp_path, dividend=dividend))
    assert len(result['bundle']['events']) == int(dividend)
    assert result['bundle']['profile'] == 'SYNTHETIC'
    assert result['bundle']['company_actions'] == 'OBSERVED_CASH_DIVIDEND_V1'
    assert result['evidence']['strategy_qualified'] is False
    assert result['evidence']['qualification_evidence_eligible'] is False
    if dividend:
        assert result['bundle']['daily'].prev_close.tolist() == [10., 10., 9.9]


@pytest.mark.parametrize('changes, reason', [({'coverage': False}, 'EVIDENCE_MISSING'),
                                           ({'late': True}, 'BEFORE_RECORD_CLOSE')])
def test_missing_coverage_and_backfilled_announcement_rejected(tmp_path, changes, reason):
    with pytest.raises(ValueError, match=reason):
        build_rule_confirmation_bundle(**fixture(tmp_path, **changes))


def test_v2_preserves_inventory_and_order_guards(tmp_path):
    args = fixture(tmp_path)
    with pytest.raises(ValueError, match='REORDERED'):
        build_rule_confirmation_bundle(**{**args, 'snapshot_ids': list(reversed(args['snapshot_ids']))})
    with pytest.raises(ValueError, match='FUTURE_FROZEN'):
        build_rule_confirmation_bundle(**{**args, 'frozen_at': '2026-01-02T10:00:00+08:00'})


def test_cash_envelope_does_not_bypass_original_tdx_price_or_action_flag():
    day, stamp = DAYS[2], '2026-01-06T15:30:00+08:00'
    data = payload(day)
    data['bars'][0].update(open=9.9, high=9.9, low=9.9, close=9.9, prev_close=9.9)
    entries = [('get_match_stkinfo', {'key_word': '茅台'}, {'Value': []}),
        ('get_trading_calendar', {'market': 'SH', 'start_time': str(day), 'end_time': str(day)}, {'Value': [day]}),
        ('get_market_snapshot', {'stock_code': SYMBOL}, {SYMBOL: {'LastClose': 9.9}}),
        ('get_stock_info', {'stock_code': SYMBOL}, {SYMBOL: dict(DelayMin=0, HSStockKind='1', J_start='19910101',
                                                              IsQuitGP=0, IsSTGP=0, TodayDRFlag=1)}),
        ('get_more_info', {'stock_code': SYMBOL, 'field_list': ['HqDate', 'TPFlag', 'fHSL']},
         {SYMBOL: dict(HqDate=day, TPFlag=0, fHSL=1.)}),
        ('get_market_data', {'stock_list': [SYMBOL], 'period': '1d', 'start_time': str(day), 'end_time': str(day),
                            'count': 0, 'dividend_type': 'none', 'fill_data': False},
         {SYMBOL: dict(Date=[day], Open=[9.9], High=[9.9], Low=[9.9], Close=[9.9], Volume=[1000.], Amount=[10.])})]
    snapshot = {'provider': 'TDX_TQ_LOCAL_UNCACHED_V1', 'phase': 'CLOSE', 'market_date': day,
                'capture_started_at': stamp, 'received_at': stamp, 'payload': data,
                'source_responses': [{'method': m, 'params': p, 'result': r, 'received_at': stamp} for m, p, r in entries]}
    event = {'symbol': SYMBOL, 'effective_date': day}
    _verify_real_cash(snapshot, [SYMBOL], [event])
    with pytest.raises(ValueError, match='CORPORATE_ACTION_UNRESOLVED'):
        _verify_real_cash(snapshot, [SYMBOL], [])
    changed = deepcopy(snapshot)
    changed['payload']['bars'][0]['close'] = 10.
    with pytest.raises(ValueError, match='NOT_REPLAYABLE'):
        _verify_real_cash(changed, [SYMBOL], [event])
