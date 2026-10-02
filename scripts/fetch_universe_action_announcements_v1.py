"""按已冻结证券和公告窗口留存公告原件及待审文本，不生成账户条款结论。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from chanlun_trader.research.guard import ResearchDataAccessGuard
from scripts.resolve_universe_action_dates_v1 import _action_source_readonly

VERSION = 'UNIVERSE_ACTION_ANNOUNCEMENT_ACQUISITION_V1'
TARGET_VERSION = 'UNIVERSE_ACTION_ANNOUNCEMENT_TARGETS_V1'
QUERY_URL = 'https://www.cninfo.com.cn/new/hisAnnouncement/query'
PDF_LIMIT = 12_000_000
METADATA_LIMIT = 2_000_000
TIMEOUT = 30
SHANGHAI = timezone(timedelta(hours=8))
KINDS = {'SHARE_ACCOUNT_TERMS': ('实施公告',),
         'CATEGORY15_REFERENCE_PRICE': ('资本重整', '除权参考价'),
         'CORPORATE_ACTION_DATE': ('权益分派', '股本', '除权'),
         'CASH_ACTION_DATE': ('权益分派', '利润分配')}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _safe(path):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError('ANNOUNCEMENT_PATH_REDIRECTED')
    return path


def _load(path):
    return json.loads(_safe(path).read_text(encoding='utf-8'))


def _sha(path):
    return hashlib.sha256(_safe(path).read_bytes()).hexdigest()


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _write(path, value):
    _safe(path).parent.mkdir(parents=True, exist_ok=True)
    with _safe(path).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def _write_bytes(path, raw):
    with _safe(path).open('xb') as stream:
        stream.write(raw)


def _day(value):
    text = str(value).replace('-', '')
    if not re.fullmatch(r'\d{8}', text):
        raise ValueError('ANNOUNCEMENT_DATE_INVALID')
    datetime.strptime(text, '%Y%m%d')
    return int(text)


def _iso(day):
    return datetime.strptime(str(day), '%Y%m%d').strftime('%Y-%m-%d')


def _window(value, effective):
    if not isinstance(value, dict) or set(value) != {'start', 'end'}:
        raise ValueError('ANNOUNCEMENT_PUBLICATION_WINDOW_REQUIRED')
    start, end = _day(value['start']), _day(value['end'])
    ResearchDataAccessGuard().check_range(start, end, 'announcement frozen publication window')
    if start > end or end > effective:
        raise ValueError('ANNOUNCEMENT_WINDOW_AFTER_EFFECTIVE_DATE')
    # 查询不扩展为全年公告；窗口最多覆盖事前 14 天和事件当日。
    if (datetime.strptime(str(end), '%Y%m%d') - datetime.strptime(str(start), '%Y%m%d')).days > 14:
        raise ValueError('ANNOUNCEMENT_PUBLICATION_WINDOW_TOO_WIDE')
    return {'start': start, 'end': end}


def _org_id(target):
    org_id = target.get('cninfo_org_id')
    if org_id is None or org_id == '':
        raise ValueError('ANNOUNCEMENT_FROZEN_ORG_ID_REQUIRED')
    if not isinstance(org_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', org_id):
        raise ValueError('ANNOUNCEMENT_ORG_ID_INVALID')
    return org_id


def _validate_targets(targets_path, catalog_path):
    value, catalog = _load(targets_path), _load(catalog_path)
    if value.get('version') != TARGET_VERSION or not isinstance(value.get('targets'), list):
        raise ValueError('ANNOUNCEMENT_TARGETS_INVALID')
    sources = catalog.get('sources')
    if not isinstance(sources, list):
        raise ValueError('ANNOUNCEMENT_SOURCE_CATALOG_INVALID')
    result, cached, seen = [], {}, set()
    for supplied in value['targets']:
        if not isinstance(supplied, dict):
            raise ValueError('ANNOUNCEMENT_TARGET_INVALID')
        target = dict(supplied)
        symbol, kind = target.get('symbol'), target.get('kind')
        if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.(SZ|SH)', symbol) or kind not in KINDS:
            raise ValueError('ANNOUNCEMENT_TARGET_SECURITY_OR_KIND_INVALID')
        effective = _day(target.get('effective_date'))
        ResearchDataAccessGuard().check_date(effective, 'announcement target effective date')
        primary = _window(target.get('publication_window'), effective)
        fallback = target.get('fallback_publication_window')
        if fallback is not None:
            fallback = _window(fallback, effective)
            if not fallback['start'] <= primary['start'] <= primary['end'] <= fallback['end']:
                raise ValueError('ANNOUNCEMENT_FALLBACK_MUST_INCLUDE_PRIMARY')
        _org_id(target)
        digest = target.get('source_response_sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('ANNOUNCEMENT_SOURCE_SHA_REQUIRED')
        matches = [source for source in sources if source.get('sha256') == digest
                   and source.get('symbol') == symbol]
        if len(matches) != 1:
            raise ValueError('ANNOUNCEMENT_UNIQUE_REGISTERED_SOURCE_REQUIRED')
        source = matches[0]
        # 必须先核验登记的整个原件范围，再读取该原件的头、内容和 SHA。
        ResearchDataAccessGuard().check_range(_day(source['physical_start']), _day(source['physical_end']),
                                             'announcement whole registered original')
        if source.get('request_verified') is not True:
            raise ValueError('ANNOUNCEMENT_VERIFIED_SOURCE_REQUIRED')
        required_kind = 'DIVIDEND' if kind in {'SHARE_ACCOUNT_TERMS', 'CASH_ACTION_DATE'} else 'ADJUST'
        if source.get('kind') != required_kind:
            raise ValueError('ANNOUNCEMENT_SOURCE_KIND_CONFLICT')
        _safe(source['path'])
        if digest not in cached:
            cached[digest] = _action_source_readonly(source)
        rows, binding = cached[digest]
        own = [(index, row) for index, row in enumerate(rows)
               if _day(row.get('dividOperateDate')) == effective]
        if len(own) != 1:
            raise ValueError('ANNOUNCEMENT_UNIQUE_SOURCE_EVENT_REQUIRED')
        row_index, row = own[0]
        if row.get('code') != symbol[-2:].lower() + '.' + symbol[:6]:
            raise ValueError('ANNOUNCEMENT_SOURCE_SECURITY_CONFLICT')
        plan_date = _day(row['dividPlanDate']) if row.get('dividPlanDate') else None
        if plan_date is not None and not primary['start'] <= plan_date <= primary['end']:
            raise ValueError('ANNOUNCEMENT_PRIMARY_WINDOW_MUST_BIND_PLAN_DATE')
        key = f'{symbol}_{effective}_{kind}'
        if key in seen:
            raise ValueError('ANNOUNCEMENT_TARGET_DUPLICATE')
        seen.add(key)
        target.update(effective_date=effective, publication_window=primary,
            source_plan_date=plan_date, source_row_index=row_index, source_row_sha256=_hash(row),
            source_binding=binding, target_id=key)
        if fallback is not None:
            target['fallback_publication_window'] = fallback
        result.append(target)
    return result


def _validate_url(url, *, published_date=None, symbol=None, announcement_id=None):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.username or parts.password or parts.fragment
            or parts.port not in (None, 443) or '%' in parts.path or '..' in parts.path):
        raise ValueError('ANNOUNCEMENT_OFFICIAL_HTTPS_URL_REQUIRED')
    if published_date is None:
        if url != QUERY_URL:
            raise ValueError('ANNOUNCEMENT_QUERY_ENDPOINT_NOT_ALLOWED')
        return
    if parts.query:
        raise ValueError('ANNOUNCEMENT_PDF_QUERY_NOT_ALLOWED')
    host = parts.hostname
    cninfo = host in {'www.cninfo.com.cn', 'static.cninfo.com.cn'}
    if cninfo:
        match = re.fullmatch(r'/finalpage/(\d{4}-\d{2}-\d{2})/(\d{1,24})\.[Pp][Dd][Ff]', parts.path)
    elif host in {'www.sse.com.cn', 'static.sse.com.cn'}:
        match = re.fullmatch(r'/disclosure/listedinfo/announcement/c/new/(\d{4}-\d{2}-\d{2})/([A-Za-z0-9_-]{1,100})\.[Pp][Dd][Ff]', parts.path)
        if match and (not symbol or not match[2].startswith(symbol[:6] + '_')):
            raise ValueError('ANNOUNCEMENT_SSE_PDF_SECURITY_CONFLICT')
    elif host in {'www.szse.cn', 'static.szse.cn', 'disc.static.szse.cn'}:
        match = re.fullmatch(r'/download/disc/disk\d{2}/finalpage/(\d{4}-\d{2}-\d{2})/([A-Za-z0-9_-]{1,100})\.[Pp][Dd][Ff]', parts.path)
    else:
        raise ValueError('ANNOUNCEMENT_PDF_HOST_NOT_ALLOWED')
    if not match or _day(match[1]) != published_date:
        raise ValueError('ANNOUNCEMENT_PDF_PUBLICATION_DATE_CONFLICT')
    ResearchDataAccessGuard().check_date(published_date, 'announcement PDF publication date')
    if cninfo and announcement_id is not None and match[2] != str(announcement_id):
        raise ValueError('ANNOUNCEMENT_PDF_ID_CONFLICT')


class _OfficialRedirects(HTTPRedirectHandler):
    def __init__(self, validation):
        self.validation = validation

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl, **self.validation)
        if self.validation.get('published_date') is None:
            # urllib 的 301/302 会把 POST 改为 GET，不能丢失限定 stock 的查询参数。
            raise ValueError('ANNOUNCEMENT_QUERY_REDIRECT_NOT_ALLOWED')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url, *, data=None, limit, published_date=None, symbol=None, announcement_id=None):
    validation = {'published_date': published_date, 'symbol': symbol, 'announcement_id': announcement_id}
    _validate_url(url, **validation)
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.cninfo.com.cn/'}
    if data is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    opener = build_opener(_OfficialRedirects(validation))
    with opener.open(Request(url, data=data, headers=headers), timeout=TIMEOUT) as response:
        final_url = response.geturl()
        _validate_url(final_url, **validation)
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('ANNOUNCEMENT_RESPONSE_TOO_LARGE')
    return raw, final_url


def _published(value):
    if isinstance(value, int) or (isinstance(value, str) and re.fullmatch(r'\d{13}', value)):
        instant = datetime.fromtimestamp(int(value) / 1000, timezone.utc).astimezone(SHANGHAI)
    elif isinstance(value, str):
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=SHANGHAI)
        instant = instant.astimezone(SHANGHAI)
    else:
        raise ValueError('ANNOUNCEMENT_METADATA_PUBLICATION_TIME_REQUIRED')
    day = int(instant.strftime('%Y%m%d'))
    ResearchDataAccessGuard().check_date(day, 'announcement metadata publication date')
    return day, instant.isoformat()


def _title_allowed(title, kind):
    title = re.sub(r'\s+', '', html.unescape(re.sub(r'<[^>]*>', '', title)))
    implementation = (re.search(r'(?:权益分派|权益分配|利润分配|分红派息|转增股本)的?实施的?公告$', title) is not None
        and re.search(r'预案|提示|更正|补充|修订|修改|取消|撤销|延期|终止', title) is None)
    if kind in {'SHARE_ACCOUNT_TERMS', 'CASH_ACTION_DATE'}:
        allowed = implementation
    elif kind == 'CATEGORY15_REFERENCE_PRICE':
        allowed = '资本重整' in title or '除权参考价' in title
    else:
        allowed = (implementation or '除权参考价' in title
            or ('股本' in title and any(word in title for word in ('变动', '变化', '调整')))
            or ('资本公积' in title and '转增' in title and '实施' in title)
            or ('重整计划' in title and any(word in title for word in ('实施', '执行完毕', '执行完成'))))
    return title, allowed


def _parse_metadata(raw, target, window):
    org_id = _org_id(target)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('ANNOUNCEMENT_METADATA_RESPONSE_INVALID')
    items = value.get('announcements')
    if items is None and value.get('totalAnnouncement') == 0 and value.get('hasMore') is False:
        items = []
    if (not isinstance(items, list) or len(items) > 30
            or value.get('hasMore') not in (True, False, None)):
        raise ValueError('ANNOUNCEMENT_METADATA_RESPONSE_INVALID')
    candidates, rejected = [], []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('announcementTitle'), str):
            raise ValueError('ANNOUNCEMENT_METADATA_ITEM_INVALID')
        day, timestamp = _published(item.get('announcementTime'))
        title, allowed = _title_allowed(item['announcementTitle'], target['kind'])
        reason = None
        if str(item.get('secCode', '')) != target['symbol'][:6]:
            reason = 'SECURITY_MISMATCH'
        elif str(item.get('orgId', '')) != org_id:
            reason = 'ORG_ID_MISMATCH'
        elif not window['start'] <= day <= window['end']:
            reason = 'PUBLICATION_OUTSIDE_FROZEN_WINDOW'
        elif not allowed:
            reason = 'TITLE_NOT_TARGET_IMPLEMENTATION_OR_REFERENCE'
        announcement_id = str(item.get('announcementId', ''))
        if not re.fullmatch(r'\d{1,24}', announcement_id):
            reason = reason or 'ANNOUNCEMENT_ID_INVALID'
        if reason:
            rejected.append({'metadata': item, 'reason': reason})
            continue
        adjunct = item.get('adjunctUrl')
        if not isinstance(adjunct, str):
            raise ValueError('ANNOUNCEMENT_METADATA_PDF_URL_REQUIRED')
        url = adjunct if urlsplit(adjunct).scheme or adjunct.startswith('//') else 'https://static.cninfo.com.cn/' + adjunct
        _validate_url(url, published_date=day, symbol=target['symbol'], announcement_id=announcement_id)
        candidates.append({'announcement_id': announcement_id, 'title': title,
            'published_date': day, 'published_at': timestamp, 'url': url, 'metadata': item})
    return candidates, rejected, value.get('hasMore') is True


def _query(target, window, keyword, page):
    stock = target['symbol'][:6] + ',' + _org_id(target)
    return {'pageNum': str(page), 'pageSize': '30',
        'column': 'sse' if target['symbol'].endswith('.SH') else 'szse',
        'tabName': 'fulltext', 'stock': stock, 'searchkey': keyword,
        'seDate': _iso(window['start']) + '~' + _iso(window['end']), 'isHLtitle': 'false'}


def _verify_artifact(root, artifact):
    if artifact.get('published_date') is not None:
        ResearchDataAccessGuard().check_date(_day(artifact['published_date']), 'announcement resumed original')
    path = _safe(artifact['path'])
    if not path.is_relative_to(root) or not path.is_file() or _sha(path) != artifact['sha256']:
        raise ValueError('ANNOUNCEMENT_RESUME_ARTIFACT_CHANGED')


def _verify_pdf_receipt(directory, target, receipt):
    identity = receipt['identity']
    day = _day(identity['published_date'])
    ResearchDataAccessGuard().check_date(day, 'announcement resumed metadata publication date')
    windows = [target['publication_window'], target.get('fallback_publication_window')]
    if not any(window and window['start'] <= day <= window['end'] for window in windows):
        raise ValueError('ANNOUNCEMENT_RESUME_PUBLICATION_WINDOW_CHANGED')
    _validate_url(identity['url'], published_date=day, symbol=target['symbol'],
                  announcement_id=identity['announcement_id'])
    document, text = receipt.get('document'), receipt.get('text')
    if document:
        if document.get('published_date') != day or document.get('source_url') != identity['url']:
            raise ValueError('ANNOUNCEMENT_RESUME_PDF_IDENTITY_CHANGED')
        _validate_url(document['final_url'], published_date=day, symbol=target['symbol'],
                      announcement_id=identity['announcement_id'])
        _verify_artifact(directory, document)
    if text:
        if (not document or text.get('original_sha256') != document['sha256']
                or text.get('source_sha256') != document['sha256'] or document.get('text') != text
                or not text.get('extractor_provenance') or not text.get('extraction_policy')):
            raise ValueError('ANNOUNCEMENT_RESUME_TEXT_BINDING_CHANGED')
        _verify_artifact(directory, text)


def _metadata_attempt(directory, target, window, keyword, page, label, *, allow_network=True):
    query = _query(target, window, keyword, page)
    receipt_path = directory / (label + '.receipt.json')
    start_path = directory / (label + '.started.json')
    if receipt_path.exists():
        receipt = _load(receipt_path)
        if receipt.get('request') != query:
            raise ValueError('ANNOUNCEMENT_RESUME_REQUEST_CHANGED')
        if receipt.get('response'):
            _verify_artifact(directory, receipt['response'])
            candidates, rejected, more = _parse_metadata(_safe(receipt['response']['path']).read_bytes(), target, window)
            if (receipt.get('candidates'), receipt.get('rejected'), receipt.get('has_more')) != (candidates, rejected, more):
                raise ValueError('ANNOUNCEMENT_RESUME_METADATA_CHANGED')
            _validate_url(receipt['final_url'])
        return receipt
    if start_path.exists():
        if _load(start_path).get('request') != query:
            raise ValueError('ANNOUNCEMENT_RESUME_REQUEST_CHANGED')
        return {'status': 'INCOMPLETE_ATTEMPT', 'request': query,
                'error': 'Original attempt has no receipt; no duplicate request was issued.'}
    if not allow_network:
        raise ValueError('ANNOUNCEMENT_RESUME_QUERY_RECEIPT_MISSING')
    _write(start_path, {'request': query, 'url': QUERY_URL, 'started_at': _now()})
    receipt = {'request': query, 'url': QUERY_URL, 'retrieved_at': _now()}
    raw = None
    try:
        raw, final_url = _fetch(QUERY_URL, data=urlencode(query).encode(), limit=METADATA_LIMIT)
        candidates, rejected, more = _parse_metadata(raw, target, window)
        path = directory / (label + '.response.json')
        _write_bytes(path, raw)
        receipt.update(status='ACQUIRED', final_url=final_url, candidates=candidates,
            rejected=rejected, has_more=more, response={'path': str(path),
                'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
    except Exception as exc:
        receipt.update(status='FAILED', error_type=type(exc).__name__, error=str(exc)[:512])
        if raw is not None:
            receipt['failed_response_sha256'] = hashlib.sha256(raw).hexdigest()
    _write(receipt_path, receipt)
    return receipt


def _extract_text(raw):
    # 由运行环境提供依赖；不安装、不绑定机器路径，缺失时原 PDF 仍保留。
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(raw))
    text = '\n\n'.join(page.extract_text() or '' for page in reader.pages)
    if not text.strip():
        raise ValueError('ANNOUNCEMENT_PDF_HAS_NO_EXTRACTABLE_TEXT')
    return text, {'tool': 'pypdf', 'version': pypdf.__version__, 'page_count': len(reader.pages),
                  'operation': 'PdfReader.page.extract_text', 'ocr_used': False}


def _download_candidate(directory, target, candidate, *, allow_network=True):
    label = 'PDF_' + candidate['announcement_id']
    receipt_path = directory / (label + '.receipt.json')
    start_path = directory / (label + '.started.json')
    identity = {key: candidate[key] for key in ('announcement_id', 'published_date', 'published_at', 'url')}
    if receipt_path.exists():
        receipt = _load(receipt_path)
        if (receipt.get('identity') != identity or receipt.get('metadata') != candidate['metadata']
                or receipt.get('metadata_response_sha256') != candidate['metadata_response_sha256']
                or receipt.get('source_response_sha256') != target['source_response_sha256']):
            raise ValueError('ANNOUNCEMENT_RESUME_PDF_IDENTITY_CHANGED')
        _verify_pdf_receipt(directory, target, receipt)
        return receipt
    artifact_path = directory / (label + '.artifact.json')
    saved = None
    if start_path.exists():
        if _load(start_path).get('identity') != identity:
            raise ValueError('ANNOUNCEMENT_RESUME_PDF_IDENTITY_CHANGED')
        if artifact_path.exists():
            saved = _load(artifact_path)
            if saved.get('identity') != identity:
                raise ValueError('ANNOUNCEMENT_RESUME_PDF_IDENTITY_CHANGED')
            _verify_artifact(directory, saved['document'])
        else:
            return {'status': 'INCOMPLETE_ATTEMPT', 'identity': identity,
                'error': 'Original attempt has no receipt; no duplicate request was issued.'}
    else:
        if not allow_network:
            raise ValueError('ANNOUNCEMENT_RESUME_PDF_RECEIPT_MISSING')
        _write(start_path, {'identity': identity, 'started_at': _now()})
    receipt = {'identity': identity, 'symbol': target['symbol'], 'effective_date': target['effective_date'],
        'source_response_sha256': target['source_response_sha256'], 'metadata': candidate['metadata'],
        'metadata_response_sha256': candidate['metadata_response_sha256'], 'retrieved_at': _now(),
        'review_status': 'UNREVIEWED', 'automatic_terms_resolution': False,
        'share_credit_date_inferred_from_listing': False}
    raw = None
    try:
        if saved:
            raw = _safe(saved['document']['path']).read_bytes()
            receipt.update(status='PDF_ACQUIRED', document=saved['document'])
        else:
            raw, final_url = _fetch(candidate['url'], limit=PDF_LIMIT,
                published_date=candidate['published_date'], symbol=target['symbol'],
                announcement_id=candidate['announcement_id'])
            if not raw.startswith(b'%PDF'):
                raise ValueError('ANNOUNCEMENT_ORIGINAL_PDF_REQUIRED')
            path = directory / (label + '.pdf')
            _write_bytes(path, raw)
            receipt.update(status='PDF_ACQUIRED', document={'path': str(path),
                'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                'published_date': candidate['published_date'], 'published_at': candidate['published_at'],
                'source_url': candidate['url'], 'url': candidate['url'], 'final_url': final_url})
            _write(artifact_path, {'identity': identity, 'document': receipt['document']})
    except Exception as exc:
        receipt.update(status='FAILED', error_type=type(exc).__name__, error=str(exc)[:512])
        if raw is not None:
            receipt['failed_response_sha256'] = hashlib.sha256(raw).hexdigest()
    if receipt.get('document'):
        try:
            text, extraction = _extract_text(raw)
        except Exception as exc:
            receipt.update(status='PDF_ACQUIRED_TEXT_FAILED', extraction_error_type=type(exc).__name__,
                           extraction_error=str(exc)[:512])
        else:
            text_path = directory / (label + '.txt')
            encoded = text.encode('utf-8')
            text_sha = hashlib.sha256(encoded).hexdigest()
            if _safe(text_path).exists():
                existing = _safe(text_path).read_bytes()
                if existing != encoded or hashlib.sha256(existing).hexdigest() != text_sha:
                    raise ValueError('ANNOUNCEMENT_RESUME_EXISTING_TEXT_CHANGED')
            else:
                _write_bytes(text_path, encoded)
            receipt.update(status='TEXT_EXTRACTED_UNREVIEWED', text={'path': str(text_path),
                'sha256': text_sha, 'bytes': len(encoded),
                'source_sha256': receipt['document']['sha256'],
                'original_sha256': receipt['document']['sha256'], 'extractor_provenance': extraction,
                'extraction_policy': 'Hash-bound original PDF; pypdf extract_text; no OCR or date inference'})
            receipt['document']['text'] = receipt['text']
    _write(receipt_path, receipt)
    return receipt


def _collect_target(directory, target, *, allow_network=True):
    queries, candidates, failures, truncated = [], {}, [], False
    windows = [target['publication_window']]
    if target.get('fallback_publication_window'):
        windows.append(target['fallback_publication_window'])
    for window_index, window in enumerate(windows):
        if window_index and (candidates or failures or truncated):
            break
        for keyword_index, keyword in enumerate(KINDS[target['kind']]):
            for page in (1, 2):
                label = f'QUERY_W{window_index}_K{keyword_index}_P{page}'
                receipt = _metadata_attempt(directory, target, window, keyword, page, label,
                                            allow_network=allow_network)
                queries.append(receipt)
                if receipt['status'] != 'ACQUIRED':
                    failures.append(label)
                    break
                for candidate in receipt['candidates']:
                    bound = dict(candidate, metadata_response_sha256=receipt['response']['sha256'])
                    existing = candidates.get(candidate['announcement_id'])
                    if existing and existing['url'] != candidate['url']:
                        raise ValueError('ANNOUNCEMENT_METADATA_DUPLICATE_ID_CONFLICT')
                    candidates[candidate['announcement_id']] = bound
                if not receipt['has_more']:
                    break
                if page == 2:
                    truncated = True
        if failures:
            break
    documents = [_download_candidate(directory, target, candidate, allow_network=allow_network)
                 for candidate in candidates.values()]
    status = ('QUERY_FAILED' if failures else 'QUERY_PAGE_LIMIT_REACHED' if truncated else
              'CANDIDATES_ACQUIRED_UNREVIEWED' if documents else 'NO_MATCHING_CANDIDATE')
    if any(document['status'] in {'FAILED', 'INCOMPLETE_ATTEMPT'} for document in documents):
        status = 'PDF_ACQUISITION_INCOMPLETE'
    return {'target': target, 'status': status, 'queries': queries, 'documents': documents,
        'review_status': 'UNREVIEWED', 'automatic_terms_resolution': False,
        'global_no_announcement_claim': False, 'completed_at': _now()}


def fetch_universe_action_announcements_v1(*, targets, source_catalog, output_dir, resume=False):
    targets_path, catalog_path = _safe(targets), _safe(source_catalog)
    normalized = _validate_targets(targets_path, catalog_path)
    out = _safe(output_dir)
    if out == targets_path.parent or not out.is_relative_to(targets_path.parent):
        raise ValueError('ANNOUNCEMENT_OUTPUT_MUST_BE_WITHIN_TARGET_PACKAGE')
    plan = {'version': VERSION, 'targets': {'path': str(targets_path), 'sha256': _sha(targets_path)},
        'source_catalog': {'path': str(catalog_path), 'sha256': _sha(catalog_path)},
        'algorithm': {'path': str(Path(__file__).absolute()), 'sha256': _sha(__file__)},
        'frozen_targets': normalized, 'no_price_read': True, 'no_strategy_performance_read': True,
        'automatic_terms_resolution': False, 'final_test_override': False,
        'network_policy': {'query_url': QUERY_URL, 'max_pages_per_keyword': 2,
            'metadata_limit_bytes': METADATA_LIMIT, 'pdf_limit_bytes': PDF_LIMIT,
            'timeout_seconds': TIMEOUT, 'single_threaded': True}}
    plan_path = out / 'READ_PLAN.json'
    if out.exists():
        if not resume or not plan_path.is_file() or _load(plan_path) != plan:
            raise ValueError('ANNOUNCEMENT_NEW_OUTPUT_OR_UNCHANGED_RESUME_REQUIRED')
    else:
        out.mkdir(parents=True)
        _write(plan_path, plan)
    summaries = sorted(out.glob('ACQUISITION_RESULT_*.json'))
    if summaries:
        previous = _load(summaries[-1])
        if previous.get('read_plan_sha256') != _sha(plan_path):
            raise ValueError('ANNOUNCEMENT_RESUME_READ_PLAN_CHANGED')
        for item in previous['results']:
            _verify_artifact(out, {'path': item['result_path'], 'sha256': item['result_sha256']})
    # 先校验全部已有原件和响应，再允许续采任何缺失 target。
    for target in normalized:
        directory = out / target['target_id']
        if directory.exists():
            _safe(directory)
            for path in directory.glob('*.receipt.json'):
                receipt = _load(path)
                if receipt.get('identity'):
                    _verify_pdf_receipt(directory, target, receipt)
                elif receipt.get('response'):
                    _verify_artifact(directory, receipt['response'])
            for path in directory.glob('*.artifact.json'):
                _verify_artifact(directory, _load(path)['document'])
    results = []
    for target in normalized:
        directory = out / target['target_id']
        result_path = directory / 'RESULT.json'
        if result_path.exists():
            result = _load(result_path)
            if result.get('target') != target:
                raise ValueError('ANNOUNCEMENT_RESUME_TARGET_CHANGED')
            replay = _collect_target(directory, target, allow_network=False)
            if any(replay[field] != result[field] for field in ('status', 'queries', 'documents')):
                raise ValueError('ANNOUNCEMENT_RESUME_RESULT_CHANGED')
        else:
            directory.mkdir(parents=True, exist_ok=True)
            result = _collect_target(directory, target)
            _write(result_path, result)
        results.append({'target_id': target['target_id'], 'status': result['status'],
                        'result_path': str(result_path), 'result_sha256': _sha(result_path)})
    summary = {'version': VERSION, 'read_plan_sha256': _sha(plan_path), 'results': results,
               'status_counts': dict(Counter(item['status'] for item in results)), 'completed_at': _now()}
    index = 1
    while (out / f'ACQUISITION_RESULT_{index:06d}.json').exists():
        index += 1
    _write(out / f'ACQUISITION_RESULT_{index:06d}.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--targets', required=True, type=Path)
    parser.add_argument('--source-catalog', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    result = fetch_universe_action_announcements_v1(targets=args.targets, source_catalog=args.source_catalog,
                                                  output_dir=args.output_dir, resume=args.resume)
    print(json.dumps({'version': VERSION, 'status_counts': result['status_counts']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
