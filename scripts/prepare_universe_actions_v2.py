"""从已核验原件物化现金及送转事件，区分价格覆盖与账户条款覆盖。"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

from scripts.prepare_universe_actions_v1 import (
    EVENT_SOURCE, COVERAGE_SOURCE, _ACTION_FIELDS, _day, _load, _normalize_action,
    _number, _read_response, _sha, _write,
)
from chanlun_trader.research.guard import ResearchDataAccessGuard


VERSION = 'UNIVERSE_CASH_AND_SHARE_ACTION_PREPARATION_V2'


def _share_event(symbol, row, source):
    """原始比例可证明价格变化，不能同时证明免税或股份到账时点。"""
    rates = [Fraction(str(_number(0 if row[k] == '' else row[k])))
             for k in ('dividStocksPs', 'dividReserveToStockPs')]
    ratio = 1 + sum(rates)
    if ratio <= 1:
        raise ValueError('SHARE_MARKET_DATE_WITHOUT_SHARE_RATIO')
    record, day, published = (_day(row[k]) for k in ('dividRegistDate', 'dividOperateDate', 'dividPlanDate'))
    if None in (record, day, published) or not published <= record < day:
        raise ValueError('SHARE_ACTION_DATES_UNKNOWN')
    listing = _day(row['dividStockMarketDate'])
    kind = 'CAPITALIZATION' if rates[0] == 0 else 'BONUS'
    return {'event_id': f'{symbol}:{day}:SHARES', 'symbol': symbol, 'event_type': kind,
        'record_date': record, 'effective_date': day, 'source_published_at': row['dividPlanDate'],
        'units': 'NEW_SHARES_PER_OLD_SHARE', 'source': EVENT_SOURCE,
        'terms': {'ratio_numerator': ratio.numerator, 'ratio_denominator': ratio.denominator,
                  'tax_rule': {'kind': 'UNKNOWN', 'source': ''}},
        'vendor_bonus_rate': str(rates[0]), 'vendor_capitalization_rate': str(rates[1]),
        'vendor_new_share_listing_date': listing,
        'share_credit_date': None, 'tradable_date': listing,
        'date_evidence': {'share_credit_date': {'kind': 'UNKNOWN', 'source': ''},
            'tradable_date': {'kind': 'SOURCE' if listing else 'UNKNOWN', 'source': source['path']}},
        'original_source_path': source['path'], 'original_source_sha256': source['sha256'],
        'historical_available_at_verified': False, 'price_version': 'CASH_AND_SHARES_V2'}


def _cash_component(symbol, row, source):
    value = _number(row['dividCashPsBeforeTax'])
    if value == 0:
        return None
    cash_row = {**row, 'dividStocksPs': '0', 'dividReserveToStockPs': '0', 'dividStockMarketDate': ''}
    event, _, unsupported = _normalize_action(symbol, cash_row, source)
    if unsupported:
        raise ValueError(unsupported)
    return event


def _document(document, effective_date):
    day = _day(document.get('published_date'))
    if day is None or day > effective_date or not document.get('source_url'):
        raise ValueError('ACTION_TERMS_DOCUMENT_NOT_KNOWN_AT_EFFECTIVE')
    ResearchDataAccessGuard().check_range(day, day, 'action terms original document')
    physical = Path(document['path']).absolute()
    if physical.resolve() != physical or _sha(physical) != document['sha256']:
        raise ValueError('ACTION_TERMS_ORIGINAL_DOCUMENT_CHANGED')
    text = document.get('text')
    if not isinstance(text, dict) or text.get('original_sha256') != document['sha256']:
        raise ValueError('ACTION_TERMS_BOUND_TEXT_REQUIRED')
    text_path = Path(text['path']).absolute()
    if text_path.resolve() != text_path or _sha(text_path) != text['sha256']:
        raise ValueError('ACTION_TERMS_DOCUMENT_TEXT_CHANGED')
    if not text.get('extraction_policy'):
        raise ValueError('ACTION_TERMS_TEXT_EXTRACTION_POLICY_REQUIRED')


def _quote(document, quote):
    text = Path(document['text']['path']).read_text(encoding='utf-8')
    if not isinstance(quote, str) or not quote or ''.join(quote.split()) not in ''.join(text.split()):
        raise ValueError('ACTION_TERMS_QUOTE_NOT_IN_BOUND_TEXT')


def _qualifications(path):
    if path is None:
        return {}
    value = _load(Path(path))
    if value.get('version') != 'UNIVERSE_SHARE_TERMS_RESOLUTIONS_V1':
        raise ValueError('SHARE_TERMS_RESOLUTION_VERSION_INVALID')
    result = {}
    for record in value['resolutions']:
        key = record['symbol'], record['effective_date']
        if key in result:
            raise ValueError('SHARE_TERMS_RESOLUTION_DUPLICATE')
        _document(record['document'], record['effective_date'])
        for document in record.get('additional_documents', []):
            _document(document, record['effective_date'])
        for evidence in record['date_evidence'].values():
            if evidence['kind'] == 'SOURCE':
                _quote(record['document'], evidence.get('evidence_quote'))
        tax = record['tax_rule']
        if tax['kind'] == 'CAPITALIZATION_SHARE_PREMIUM_EXEMPT':
            documents = [record['document'], *record.get('additional_documents', [])]
            matching = [d for d in documents if d['sha256'] == tax.get('document_sha256')]
            if len(matching) != 1:
                raise ValueError('SHARE_PREMIUM_ORIGIN_DOCUMENT_REQUIRED')
            _quote(matching[0], tax.get('evidence_quote'))
        result[key] = record
    return result


def _cash_resolutions(path):
    if path is None:
        return {}
    value = _load(Path(path))
    if value.get('version') != 'UNIVERSE_CASH_COMPONENT_RESOLUTIONS_V1':
        raise ValueError('CASH_COMPONENT_RESOLUTION_VERSION_INVALID')
    result = {}
    for record in value['resolutions']:
        key = record['symbol'], record['effective_date']
        if key in result:
            raise ValueError('CASH_COMPONENT_RESOLUTION_DUPLICATE')
        _document(record['document'], record['effective_date'])
        projects = record['components']
        if (len(projects) < 2 or len({p['project_id'] for p in projects}) != len(projects)
                or any(not p['project_id'] or not p.get('evidence_quote') for p in projects)):
            raise ValueError('CASH_COMPONENT_PROJECT_EVIDENCE_REQUIRED')
        for project in projects:
            _quote(record['document'], project['evidence_quote'])
        if 'price_cash_per_share' in record:
            _quote(record['document'], record.get('price_evidence_quote'))
        result[key] = record
    return result


def _missing_share_information(event):
    missing = []
    tax = event['terms']['tax_rule']
    if tax.get('kind') == 'UNKNOWN' or not tax.get('source'):
        missing.append('CAPITAL_RESERVE_ORIGIN_OR_BONUS_TAX_RULE')
    for key in ('share_credit_date', 'tradable_date'):
        evidence = event.get('date_evidence', {}).get(key, {})
        if not event.get(key) or evidence.get('kind') not in {'SOURCE', 'MODELED'} or not evidence.get('source'):
            missing.append(key.upper() + '_EVIDENCE')
    return missing or ['SHARE_ACCOUNT_TERMS_CONFLICT_REQUIRES_REVIEW']


def prepare_universe_actions_v2(*, manifest, source_catalog, action_gaps,
        output_dir, manifest_name='manifest_actions_v2.json', action_resolutions=None, share_terms=None,
        feature_start=None, account_end=None, cash_components=None):
    manifest_path = Path(manifest).absolute()
    original = _load(manifest_path)
    scope = original.get('universe_scope', original)
    start = int(feature_start or scope['start'])
    end = int(account_end or scope['end'])
    ResearchDataAccessGuard().check_range(start, end, 'cash and share preparation window')
    root, out = manifest_path.parent, Path(output_dir).absolute()
    if out.resolve() != out or not out.is_relative_to(root) or out.exists():
        raise ValueError('ACTION_V2_NEW_OUTPUT_REQUIRED')
    destination = root / manifest_name
    if (Path(manifest_name).name != manifest_name or not manifest_name.endswith('.json')
            or destination.exists() or destination == manifest_path):
        raise ValueError('ACTION_V2_NEW_MANIFEST_REQUIRED')
    old_catalog_path, old_gaps_path = Path(source_catalog), Path(action_gaps)
    catalog, old_gaps = _load(old_catalog_path), _load(old_gaps_path)
    qualifications = _qualifications(share_terms)
    cash_resolutions = _cash_resolutions(cash_components)
    accepted = set()
    receipt = None
    if action_resolutions:
        receipt = _load(Path(action_resolutions))
        if (receipt['input_catalog']['sha256'] != _sha(old_catalog_path)
                or receipt['input_gaps']['sha256'] != _sha(old_gaps_path)):
            raise ValueError('ACTION_DATE_RESOLUTION_INPUT_CHANGED')
        from scripts.resolve_universe_action_dates_v1 import verify_action_date_resolution_receipt_v1
        accepted = verify_action_date_resolution_receipt_v1(Path(action_resolutions),
            source_catalog=old_catalog_path, action_gaps=old_gaps_path, manifest=manifest_path)
    targets = sorted({r['symbol'] for r in original['master']['records']})
    rows_by_symbol, verified_sources = defaultdict(list), []
    out.mkdir()
    for source in catalog['sources']:
        if source['symbol'] not in targets:
            continue
        item = {**source, 'path': Path(source['path'])}
        if source['origin'] == 'LEGACY_REGISTERED_ORIGINAL_RESPONSE':
            # 沿用原V1登记语义：旧响应不一定有接收时刻，不能伪造采集器回执。
            item.pop('requested_at_utc', None)
            item['physical_metadata'] = {'start': source['physical_start'], 'end': source['physical_end']}
        rows, evidence = _read_response(item, out / 'PHYSICAL_READ_EVENTS.jsonl')
        if source['origin'] == 'LEGACY_REGISTERED_ORIGINAL_RESPONSE':
            evidence['legacy_receipt_clock_verified'] = False
        verified_sources.append(evidence)
        if source['kind'] == 'DIVIDEND':
            for row in rows:
                day = _day(row.get('dividOperateDate'))
                if day is not None and start <= day <= end:
                    rows_by_symbol[source['symbol']].append((row, source))
    gaps = [g for g in old_gaps if g['reason'] not in {
        'UNSUPPORTED_NONCASH_CORPORATE_ACTION', 'DUPLICATE_ACTION_TERMS_CONFLICT'}
        and not (g['reason'] == 'ADJUST_AND_ACTION_DATES_CONFLICT'
                 and (g['symbol'], g['effective_date']) in accepted)]
    events, merged_components = [], []
    for symbol in targets:
        by_day = defaultdict(dict)
        for row, source in rows_by_symbol[symbol]:
            identity = hashlib.sha256(json.dumps({k: row[k] for k in sorted(_ACTION_FIELDS)},
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            by_day[_day(row['dividOperateDate'])].setdefault(identity, (row, source))
        for day, unique in sorted(by_day.items()):
            components = list(unique.values())
            own, share_count = [], 0
            try:
                for row, source in components:
                    rates = [_number(0 if row[k] == '' else row[k]) for k in
                             ('dividStocksPs', 'dividReserveToStockPs')]
                    cash = _cash_component(symbol, row, source)
                    if cash:
                        own.append(cash)
                    if any(rates) or row['dividStockMarketDate']:
                        event = _share_event(symbol, row, source)
                        share_count += 1
                        resolution = qualifications.get((symbol, day))
                        if resolution:
                            if source['sha256'] != resolution['source_response_sha256']:
                                raise ValueError('SHARE_TERMS_RESPONSE_IDENTITY_CONFLICT')
                            event.update({k: deepcopy(resolution[k]) for k in
                                          ('share_credit_date', 'tradable_date', 'date_evidence')})
                            event['terms']['tax_rule'] = deepcopy(resolution['tax_rule'])
                            event['terms_document'] = deepcopy(resolution['document'])
                            event['terms_resolution'] = deepcopy(resolution)
                            event['terms_interpretation'] = 'SOURCE_BOUND_REVIEWED_DECLARATION'
                            event['automatic_document_semantics_verified'] = False
                        own.append(event)
                if share_count > 1:
                    raise ValueError('SAME_DATE_MULTIPLE_SHARE_COMPONENTS_UNSUPPORTED')
                cash = [e for e in own if e['event_type'] == 'CASH_DIVIDEND']
                if len(cash) > 1:
                    if (len({e['record_date'] for e in cash}) != 1
                            or len({e['payment_date'] for e in cash}) != 1):
                        raise ValueError('SAME_DATE_CASH_TERMS_NOT_COMPATIBLE')
                    resolution = cash_resolutions.get((symbol, day))
                    if not resolution:
                        raise ValueError('SAME_DATE_CASH_DISTINCT_PROJECT_EVIDENCE_REQUIRED')
                    if (sorted(Fraction(str(p['cash_per_share'])) for p in resolution['components'])
                            != sorted(Fraction(str(e['terms']['cash_per_share'])) for e in cash)
                            or set(resolution['source_response_sha256s'])
                                != {e['original_source_sha256'] for e in cash}
                            or resolution['record_date'] != cash[0]['record_date']
                            or resolution['payment_date'] != cash[0]['payment_date']):
                        raise ValueError('CASH_COMPONENT_SOURCE_OR_TERMS_CONFLICT')
                    merged = deepcopy(cash[0])
                    merged['terms']['cash_per_share'] = sum(
                        Fraction(str(e['terms']['cash_per_share'])) for e in cash)
                    merged['terms']['cash_per_share'] = float(merged['terms']['cash_per_share'])
                    merged['source_published_at'] = max(e['source_published_at'] for e in cash)
                    merged['price_version'] = 'CASH_AND_SHARES_V2'
                    merged['cash_components'] = [deepcopy(e) for e in cash]
                    merged['cash_component_resolution'] = deepcopy(resolution)
                    merged['terms_interpretation'] = 'SOURCE_BOUND_REVIEWED_DECLARATION'
                    merged['automatic_document_semantics_verified'] = False
                    if 'price_cash_per_share' in resolution:
                        price_cash = resolution['price_cash_per_share']
                        if (type(price_cash) not in (int, float) or not math.isfinite(price_cash)
                                or price_cash < 0 or not resolution.get('price_evidence_quote')):
                            raise ValueError('CASH_COMPONENT_PRICE_REFERENCE_EVIDENCE_REQUIRED')
                        merged['price_terms'] = {'cash_per_share': price_cash,
                            'source': deepcopy(resolution['document']),
                            'evidence_quote': resolution['price_evidence_quote']}
                    merged_components.append({'symbol': symbol, 'effective_date': day,
                        'cash_per_share': merged['terms']['cash_per_share'],
                        'components': [e['terms']['cash_per_share'] for e in cash],
                        'status': 'DISTINCT_SOURCE_COMPONENTS_COMBINED_NOT_DROPPED'})
                    own = [e for e in own if e['event_type'] != 'CASH_DIVIDEND'] + [merged]
                elif share_count:
                    for e in own:
                        e['price_version'] = 'CASH_AND_SHARES_V2'
                events.extend(own)
            except (ValueError, KeyError, TypeError) as exc:
                gaps.append({'symbol': symbol, 'year': day // 10000, 'effective_date': day,
                    'kind': 'CORPORATE_ACTION', 'reason': str(exc), 'status': 'UNKNOWN'})
    events.sort(key=lambda e: (e['symbol'], e['effective_date'], e['event_id']))
    price_coverage, account_gaps = [], []
    for symbol in targets:
        price_complete = not any(g['symbol'] == symbol for g in gaps)
        shares = [e for e in events if e['symbol'] == symbol and e['event_type'] != 'CASH_DIVIDEND']
        for e in shares:
            try:
                from chanlun_trader.research_factory.universe_corporate_accounting_v2 import UniverseCorporateAccountingV2
                UniverseCorporateAccountingV2(0, [e], 'SHARE_TERMS_VALIDATION')
            except (ValueError, KeyError, TypeError) as exc:
                account_gaps.append({'symbol': symbol, 'effective_date': e['effective_date'],
                    'year': e['effective_date'] // 10000, 'kind': 'SHARE_ACCOUNT_TERMS',
                    'reason': str(exc), 'status': 'UNKNOWN',
                    'original_source_path': e['original_source_path'],
                    'original_source_sha256': e['original_source_sha256'],
                    'missing_information': _missing_share_information(e)})
        complete = price_complete and not any(g['symbol'] == symbol for g in account_gaps)
        price_coverage.append({'symbol': symbol, 'symbols': [symbol], 'start': start, 'end': end,
            'source': COVERAGE_SOURCE, 'complete': price_complete,
            'account_complete': complete, 'coverage_version': 'CASH_AND_SHARES_V2',
            'event_types': ['CASH_DIVIDEND', 'BONUS', 'CAPITALIZATION'],
            'historical_available_at_verified': False, 'independent_confirmation_eligible': False})
    new = deepcopy(original)
    new['files'] = {n:f for n,f in new['files'].items() if f['kind'] not in {'EVENTS', 'CORPORATE_ACTION_COVERAGE'}}
    for name, value, kind, source in [
        ('EVENTS.json', events, 'EVENTS', EVENT_SOURCE),
        ('CORPORATE_ACTION_COVERAGE.json', price_coverage, 'CORPORATE_ACTION_COVERAGE',
         COVERAGE_SOURCE)]:
        path = out / name
        _write(path, value)
        new['files'][path.relative_to(root).as_posix()] = {'kind': kind, 'format': 'JSON',
            'start': start, 'end': end, 'sha256': _sha(path), 'source_id': source}
    _write(out / 'PRICE_GAPS.json', gaps)
    _write(out / 'ACCOUNT_TERMS_GAPS.json', account_gaps)
    _write(out / 'MERGED_CASH_COMPONENTS.json', merged_components)
    _write(out / 'SOURCE_CATALOG.json', {'version': VERSION, 'sources': verified_sources,
        'original_catalog': {'path': str(old_catalog_path.absolute()), 'sha256': _sha(old_catalog_path)},
        'date_resolution': {'path': str(Path(action_resolutions).absolute()), 'sha256': _sha(Path(action_resolutions))}
                            if action_resolutions else None,
        'share_terms': {'path': str(Path(share_terms).absolute()), 'sha256': _sha(Path(share_terms))} if share_terms else None,
        'cash_components': {'path': str(Path(cash_components).absolute()), 'sha256': _sha(Path(cash_components))} if cash_components else None})
    new['corporate_actions_complete'] = all(r['account_complete'] for r in price_coverage)
    new['corporate_action_preparation'] = {'version': VERSION, 'historical_availability': 'MODELED',
        'price_covered': sum(r['complete'] for r in price_coverage),
        'account_terms_covered': sum(r['account_complete'] for r in price_coverage),
        'source_catalog': str((out/'SOURCE_CATALOG.json').relative_to(root)),
        'source_catalog_sha256': _sha(out/'SOURCE_CATALOG.json')}
    _write(destination, new)
    summary = {'version': VERSION, 'target_count': len(targets), 'event_count': len(events),
        'merged_cash_group_count': len(merged_components),
        'price_covered_symbol_count': sum(r['complete'] for r in price_coverage),
        'account_terms_covered_symbol_count': sum(r['account_complete'] for r in price_coverage),
        'price_gap_reason_counts': dict(Counter(g['reason'] for g in gaps)),
        'account_gap_reason_counts': dict(Counter(g['reason'] for g in account_gaps)),
        'new_manifest': str(destination), 'new_manifest_sha256': _sha(destination),
        'account_executed': False, 'budget_created': False, 'strategy_qualified': False}
    _write(out/'PREPARATION_COMPLETE.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('manifest', 'source-catalog', 'action-gaps', 'output-dir'):
        parser.add_argument('--'+key, required=True)
    parser.add_argument('--manifest-name', default='manifest_actions_v2.json')
    parser.add_argument('--action-resolutions')
    parser.add_argument('--share-terms')
    parser.add_argument('--cash-components')
    parser.add_argument('--feature-start', type=int)
    parser.add_argument('--account-end', type=int)
    print(json.dumps(prepare_universe_actions_v2(**vars(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
