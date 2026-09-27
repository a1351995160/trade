"""实时事件接收薄适配；资产、应收与幂等派息仍由原公司行动账本负责。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

from ..engine.corporate_accounting_v1 import (
    CorporateActionAccountingV1, CorporateActionError, at_open, identity,
)
from ..engine.individual_dividend_accounting_v1 import IndividualDividendAccountingV1, TAX_KIND


def _timestamp(value):
    try:
        result = pd.Timestamp(value)
        if pd.isna(result) or result.tzinfo is None:
            raise ValueError()
        return result.tz_convert('Asia/Shanghai')
    except (TypeError, ValueError):
        raise CorporateActionError('ACTION_AWARE_TIMESTAMP_REQUIRED') from None


def validate_action_envelope(envelope, *, asof):
    """核验采集方传入的证据绑定，不把调用者声明当作供应商真实性认证。"""
    try:
        value = deepcopy(envelope)
        event = value['event']
        if value['version'] != 'CorporateActionEnvelopeV1':
            raise CorporateActionError('ACTION_ENVELOPE_VERSION_UNSUPPORTED')
        if value['mode'] != 'OBSERVED' or type(value['revision']) is not int or value['revision'] != 1:
            raise CorporateActionError('ACTION_REVISION_OR_OBSERVATION_UNSUPPORTED')
        if value.get('supersedes'):
            raise CorporateActionError('ACTION_CORRECTION_REQUIRES_RECONCILIATION')
        if not re.fullmatch(r'[0-9a-f]{64}', value['source_sha256']):
            raise CorporateActionError('ACTION_SOURCE_DIGEST_REQUIRED')
        if any(not isinstance(value[key], str) or not value[key].strip()
               or value[key] == 'UNKNOWN' for key in ('source_ref', 'provider')):
            raise CorporateActionError('ACTION_SOURCE_REQUIRED')
        if not isinstance(event['event_id'], str) or not event['event_id']:
            raise CorporateActionError('ACTION_ID_REQUIRED')
        if not re.fullmatch(r'\d{6}\.(SH|SZ)', event['symbol']):
            raise CorporateActionError('ACTION_SYMBOL_INVALID')
        if event['event_type'] != 'CASH_DIVIDEND':
            raise CorporateActionError('DYNAMIC_ACTION_TYPE_UNSUPPORTED')
        for key in ('record_date', 'effective_date', 'payment_date'):
            if type(event[key]) is not int or len(str(event[key])) != 8:
                raise CorporateActionError('ACTION_DATE_INVALID')
            at_open(event[key])
        published = _timestamp(event['source_published_at'])
        received = _timestamp(value['received_at'])
        now = _timestamp(asof)
        record_close = at_open(event['record_date']).normalize() + pd.Timedelta(hours=15)
        if not published <= received <= now < record_close:
            raise CorporateActionError('ACTION_NOT_RECEIVED_BEFORE_RECORD_CLOSE')
        # 原账本集中验证单位、到账顺序和显式税规则；验证失败不污染运行中账户。
        ledger_type = (IndividualDividendAccountingV1
                       if event['terms']['tax_rule']['kind'] == TAX_KIND
                       else CorporateActionAccountingV1)
        probe = ledger_type(0, [], 'VALIDATE_ONLY')
        probe._cash_terms(event)
        identity(value)
        return value
    except (KeyError, TypeError):
        raise CorporateActionError('ACTION_ENVELOPE_FIELDS_MISSING') from None


def accept_action(ledger, envelope, *, asof):
    """在调用者已有运行锁内执行，并与账本checkpoint一起持久化。

    不修改历史事件；同一封套重试返回原回执。崩溃恢复由调用者既有
    checkpoint/重放协议负责，禁止仅保存事件列表而丢失账户状态。
    """
    if not isinstance(ledger, CorporateActionAccountingV1):
        raise CorporateActionError('CORPORATE_ACTION_LEDGER_REQUIRED')
    ledger._check_active()
    digest = identity(envelope)
    receipts = getattr(ledger, 'action_acceptance_receipts', {})
    event_id = envelope.get('event', {}).get('event_id')
    if event_id in receipts:
        receipt = receipts[event_id]
        if receipt['envelope_hash'] != digest:
            raise CorporateActionError('ACTION_CORRECTION_REQUIRES_RECONCILIATION')
        return deepcopy(receipt)
    value = validate_action_envelope(envelope, asof=asof)
    event = value['event']
    # 同一账户不能混用税制，也不在收到事件时悄悄更换账本类型。
    probe = type(ledger)(0, [event], 'VALIDATE_ONLY')
    probe._cash_terms(event)
    if any(old['event_id'] == event_id for old in ledger.events):
        raise CorporateActionError('ACTION_ALREADY_EXISTS_WITHOUT_ACCEPTANCE_RECEIPT')
    now = _timestamp(asof)
    record_close = at_open(event['record_date']).normalize() + pd.Timedelta(hours=15)
    timestamps = [snapshot.timestamp for snapshot in ledger.snapshots]
    timestamps += [row['timestamp'] for row in ledger.action_audit]
    timestamps += [row['fill_time'] for row in ledger.executed_fills]
    if any(_timestamp(ts) > now or _timestamp(ts) >= record_close for ts in timestamps):
        raise CorporateActionError('ACTION_LATE_ACCOUNT_STATE_REQUIRES_RECONCILIATION')
    before = ledger.checkpoint()['checkpoint_hash']
    events = [*deepcopy(ledger.events), event]
    receipt = {'version': 'CorporateActionAcceptanceV1', 'event_id': event_id,
               'envelope_hash': digest, 'source_sha256': value['source_sha256'],
               'source_ref': value['source_ref'], 'provider': value['provider'],
               'received_at': value['received_at'], 'accepted_at': now.isoformat(),
               'before_checkpoint_hash': before, 'event_hash': identity(event),
               'events_hash': identity(events)}
    receipt['receipt_hash'] = identity(receipt)
    ledger.events = events
    ledger.events_hash = receipt['events_hash']
    ledger.action_acceptance_receipts = {**receipts, event_id: receipt}
    return deepcopy(receipt)


def action_reconciliation(ledger):
    """提供独立核账输入；此回执本身不是独立核账通过证明。"""
    return deepcopy({'events': ledger.events, 'events_hash': ledger.events_hash,
        'acceptances': getattr(ledger, 'action_acceptance_receipts', {}),
        'entitlements': ledger.entitlements, 'receivables': ledger.receivables,
        'payments': sorted(ledger.payments), 'applied': sorted(ledger.applied),
        'audit': ledger.action_audit, 'cash': ledger.cash,
        'dividend_lots': getattr(ledger, 'dividend_lots', {}),
        'dividend_tax_withheld': getattr(ledger, 'dividend_tax_withheld', 0.0),
        'cash_receivable': ledger.cash_receivable, 'equity': ledger.current_equity(),
        'checkpoint_hash': ledger.checkpoint()['checkpoint_hash']})


def _source_document(reference, digest):
    path = Path(reference)
    if not path.is_absolute() or path.resolve() != path or not path.is_file():
        raise CorporateActionError('ACTION_SOURCE_FILE_REQUIRED')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise CorporateActionError('ACTION_SOURCE_FILE_HASH_CONFLICT')
    return json.loads(raw)


def verify_action_payload(payload, *, symbols, day, asof, profile, known_envelopes=()):
    """核对阶段覆盖原件及事件原件；原件声明不提升为交易所签名认证。"""
    try:
        coverage = payload['corporate_action_coverage']
        document = _source_document(coverage['source_ref'], coverage['source_sha256'])
        if document != {k: v for k, v in coverage.items() if k not in ('source_ref', 'source_sha256')}:
            raise CorporateActionError('ACTION_COVERAGE_SOURCE_CONFLICT')
        envelopes = payload['corporate_action_envelopes']
        if (coverage['complete'] is not True or payload.get('corporate_actions_complete') is not True
                or coverage['market_date'] != day or sorted(coverage['symbols']) != sorted(symbols)
                or coverage['profile'] != profile or coverage['envelope_hashes'] != [identity(e) for e in envelopes]
                or not coverage['provider'] or _timestamp(coverage['received_at']) > _timestamp(asof)
                or int(_timestamp(coverage['received_at']).strftime('%Y%m%d')) != day):
            raise CorporateActionError('ACTION_COVERAGE_SCOPE_CONFLICT')
        if payload['corporate_actions'] != [e['event'] for e in envelopes]:
            raise CorporateActionError('ACTION_ENVELOPE_LIST_CONFLICT')
        known = {e['event']['event_id']: identity(e) for e in known_envelopes}
        seen = set()
        for envelope in envelopes:
            event = envelope['event']
            key = event['event_id']
            if event['terms']['tax_rule']['kind'] != TAX_KIND:
                raise CorporateActionError('ACTION_DYNAMIC_PAPER_INDIVIDUAL_TAX_REQUIRED')
            if key in seen or event['symbol'] not in symbols:
                raise CorporateActionError('ACTION_DUPLICATE_OR_SYMBOL_SCOPE')
            seen.add(key)
            original = _source_document(envelope['source_ref'], envelope['source_sha256'])
            if original != {'provider': envelope['provider'], 'received_at': envelope['received_at'], 'event': event}:
                raise CorporateActionError('ACTION_SOURCE_CONTENT_CONFLICT')
            if key in known:
                if known[key] != identity(envelope):
                    raise CorporateActionError('ACTION_CORRECTION_REQUIRES_RECONCILIATION')
            else:
                validate_action_envelope(envelope, asof=asof)
            if profile == 'REAL_OBSERVED' and (envelope['provider'].startswith('SYNTHETIC')
                    or event['source'].startswith('SYNTHETIC')):
                raise CorporateActionError('ACTION_SYNTHETIC_SOURCE_NOT_REAL')
        if profile == 'REAL_OBSERVED' and coverage['provider'].startswith('SYNTHETIC'):
            raise CorporateActionError('ACTION_SYNTHETIC_COVERAGE_NOT_REAL')
        return deepcopy(envelopes)
    except (KeyError, TypeError, OSError, json.JSONDecodeError):
        raise CorporateActionError('ACTION_SOURCE_OR_COVERAGE_EVIDENCE_MISSING') from None
