"""合成接收证据检验，不代表实时供应商可提供完整事件。"""
from copy import deepcopy
import hashlib
import json

import pandas as pd
import pytest

from chanlun_trader.engine.corporate_accounting_v1 import CorporateActionAccountingV1, CorporateActionError
from chanlun_trader.engine.individual_dividend_accounting_v1 import IndividualDividendAccountingV1, TAX_KIND
from chanlun_trader.engine.fill import Fill
from chanlun_trader.engine.signal import Side
from chanlun_trader.research_factory.corporate_action_lifecycle_v1 import accept_action, action_reconciliation
from chanlun_trader.research_factory.corporate_action_lifecycle_v1 import verify_action_payload
from chanlun_trader.engine.corporate_accounting_v1 import identity


def stamp(day, time='09:30'):
    return pd.Timestamp(f'2022-08-{day:02d} {time}', tz='Asia/Shanghai')


def envelope(tax='EXPLICIT_NET'):
    return {'version': 'CorporateActionEnvelopeV1', 'revision': 1, 'mode': 'OBSERVED',
        'source_sha256': 'a' * 64, 'source_ref': 'synthetic://announcement',
        'provider': 'SYNTHETIC_FIXTURE', 'received_at': stamp(1).isoformat(),
        'event': {'event_id': 'D1', 'symbol': '600000.SH', 'event_type': 'CASH_DIVIDEND',
            'record_date': 20220802, 'effective_date': 20220803, 'payment_date': 20220805,
            'source': 'SYNTHETIC', 'source_published_at': stamp(1, '08:00').isoformat(),
            'units': 'CNY_PER_SHARE', 'terms': {'cash_per_share': 1,
                'tax_rule': {'kind': tax, 'source': 'SYNTHETIC_TAX'}}}}


def buy(ledger):
    _, reason = ledger.apply_fill(Fill('b', 'o', 'S', '600000.SH', Side.BUY, 100, 10, stamp(1)))
    assert reason == 'OK'


@pytest.mark.parametrize('individual', [False, True])
def test_dynamic_action_checkpoint_payment_is_identical(individual):
    cls = IndividualDividendAccountingV1 if individual else CorporateActionAccountingV1
    env = envelope(TAX_KIND if individual else 'EXPLICIT_NET')
    ledger = cls(1000, [], 'D')
    buy(ledger)
    checkpoint_before = ledger.checkpoint()
    receipt = accept_action(ledger, env, asof=stamp(1))
    restored_before = cls.restore(checkpoint_before, [], 'D')
    assert accept_action(restored_before, env, asof=stamp(1)) == receipt
    ledger.on_close(stamp(2, '15:30'))
    ledger.on_open(stamp(3))
    assert ledger.cash == 0 and ledger.cash_receivable == 100
    restored = cls.restore(ledger.checkpoint(), ledger.events, 'D')
    assert accept_action(restored, env, asof=stamp(5)) == receipt
    ledger.on_open(stamp(5))
    restored.on_open(stamp(5))
    restored.on_open(stamp(5))
    assert action_reconciliation(restored) == action_reconciliation(ledger)
    assert restored.cash == 100 and restored.cash_receivable == 0
    if individual:
        restored.apply_fill(Fill('s', 'o2', 'S', '600000.SH', Side.SELL, 100, 9, stamp(8)))
        assert restored.dividend_tax_withheld == 20


@pytest.mark.parametrize('change', [
    lambda e: e.update(mode='HISTORICAL_MODELED'),
    lambda e: e.update(received_at='2022-08-01 09:30'),
    lambda e: e.update(received_at=stamp(3).isoformat()),
    lambda e: e.update(revision=2),
    lambda e: e.update(source_sha256='unknown'),
    lambda e: e['event'].update(payment_date=None),
    lambda e: e['event'].update(event_type='BONUS'),
    lambda e: e['event']['terms'].update(tax_rule={}),
])
def test_invalid_event_cannot_change_assets_or_registered_events(change):
    ledger = CorporateActionAccountingV1(1000, [], 'D')
    buy(ledger)
    before = ledger.checkpoint()
    env = envelope()
    change(env)
    with pytest.raises((CorporateActionError, ValueError)):
        accept_action(ledger, env, asof=stamp(1))
    assert ledger.checkpoint() == before and ledger.events == []


def test_correction_rejected_without_rewriting_entitlement():
    ledger = CorporateActionAccountingV1(1000, [], 'D')
    env = envelope()
    accept_action(ledger, env, asof=stamp(1))
    buy(ledger)
    ledger.on_close(stamp(2, '15:30'))
    before = ledger.checkpoint()
    changed = deepcopy(env)
    changed['event']['terms']['cash_per_share'] = 2
    with pytest.raises(CorporateActionError, match='CORRECTION'):
        accept_action(ledger, changed, asof=stamp(3))
    assert ledger.checkpoint() == before


def test_late_event_cannot_backdate_asof_to_evade_account_state():
    ledger = CorporateActionAccountingV1(1000, [], 'D')
    buy(ledger)
    ledger.snapshot(stamp(3))
    with pytest.raises(CorporateActionError, match='LATE_ACCOUNT_STATE'):
        accept_action(ledger, envelope(), asof=stamp(1))


def test_wrong_account_tax_profile_rejected():
    ledger = CorporateActionAccountingV1(1000, [], 'D')
    with pytest.raises(CorporateActionError):
        accept_action(ledger, envelope(TAX_KIND), asof=stamp(1))
    assert ledger.events == []


def write_source(path, document):
    raw = json.dumps(document, sort_keys=True).encode()
    path.write_bytes(raw)
    return {'source_ref': str(path.absolute()), 'source_sha256': hashlib.sha256(raw).hexdigest()}


def action_payload(tmp_path, payload, received_at, day, envelopes):
    coverage = {'profile': 'SYNTHETIC', 'provider': 'SYNTHETIC_FIXTURE', 'received_at': received_at,
        'market_date': day, 'symbols': ['000001.SZ', '600000.SH'], 'complete': True,
        'envelope_hashes': [identity(e) for e in envelopes]}
    coverage.update(write_source(tmp_path / (str(day) + received_at[11:16].replace(':', '') + '.json'), coverage))
    return {**payload, 'corporate_actions': [e['event'] for e in envelopes],
            'corporate_action_envelopes': envelopes, 'corporate_action_coverage': coverage}


def test_source_file_hash_and_coverage_are_checked(tmp_path):
    env = envelope(TAX_KIND)
    env.update(write_source(tmp_path / 'event.json', {key: env[key] for key in ('provider', 'received_at', 'event')}))
    payload = action_payload(tmp_path, {'corporate_actions_complete': True}, stamp(1).isoformat(), 20220801, [env])
    assert verify_action_payload(payload, symbols=['600000.SH', '000001.SZ'], day=20220801,
        asof=stamp(1), profile='SYNTHETIC') == [env]
    (tmp_path / 'event.json').write_text('{}')
    with pytest.raises(CorporateActionError, match='HASH_CONFLICT'):
        verify_action_payload(payload, symbols=['600000.SH', '000001.SZ'], day=20220801,
            asof=stamp(1), profile='SYNTHETIC')


def test_dynamic_paper_records_dividend_and_replays(tmp_path, paper_source):
    from test_forward_paper_v1 import paper_case, snapshot, ingest
    from chanlun_trader.research_factory.forward_paper_v1 import ForwardPaperSessionV1
    _, store, clock, kwargs, _ = paper_case(tmp_path, paper_source, members=2)
    session = ForwardPaperSessionV1.create(tmp_path / 'dynamic-paper', **kwargs,
        company_actions='OBSERVED_CASH_DIVIDEND_V1')
    env = envelope(TAX_KIND)
    env['received_at'] = '2024-08-01T14:00:00+08:00'
    env['event'].update(record_date=20240802, effective_date=20240805, payment_date=20240805,
        source_published_at='2024-08-01T13:00:00+08:00')
    env['event']['terms']['cash_per_share'] = .1
    env.update(write_source(tmp_path / 'event.json', {key: env[key] for key in ('provider', 'received_at', 'event')}))
    for phase, day, previous, price in [('CLOSE', 20240801, 13.95, 14.1),
            ('OPEN', 20240802, 14.1, 14.2), ('CLOSE', 20240802, 14.1, 14.2),
            ('OPEN', 20240805, 14.1, 14.1), ('CLOSE', 20240805, 14.1, 14.1)]:
        original = snapshot(store, clock, phase, day, previous, price)
        # 另一只股票不分红，保持其真实价格参考连续。
        if day == 20240805:
            for row in original['payload']['bars']:
                if row['symbol'] == '000001.SZ':
                    row.update(prev_close=14.2, open=14.3, high=14.3, low=14.3, close=14.3)
        payload = action_payload(tmp_path, original['payload'], clock[0].isoformat(), day,
            [env] if day == 20240801 else [])
        item = store.record_synthetic(phase=phase, market_date=day, payload=payload, received_at=clock[0].isoformat())
        result = ingest(session, store, item)
    assert result['state']['economic']['entitlements']['D1'] > 0
    assert result['state']['economic']['payments'] == ['D1']
    restored = ForwardPaperSessionV1(session.root, clock=lambda: clock[0])
    replay = restored._recover(restored.header(), restored._records(restored.header()))
    assert replay.state() == result['state']
    assert ingest(restored, store, item)['state'] == result['state']


from test_forward_paper_v1 import paper_source  # 原共享账户合成研究夹具
