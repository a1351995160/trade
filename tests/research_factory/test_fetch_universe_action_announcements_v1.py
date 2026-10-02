from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import parse_qs
from urllib.request import Request

import pytest

from chanlun_trader.research.guard import FinalTestAccessViolation
import scripts.fetch_universe_action_announcements_v1 as acquisition


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


@pytest.fixture
def package(tmp_path):
    row = {'code': 'sz.000039', 'dividOperateDate': '2023-07-20',
        'dividRegistDate': '2023-07-19', 'dividStocksPs': '0',
        'dividReserveToStockPs': '0.5', 'dividStockMarketDate': '2023-07-20',
        'dividPayDate': '2023-07-20', 'dividPlanDate': '2023-07-14',
        'dividCashPsBeforeTax': '0.1'}
    query = {'code': 'sz.000039', 'year': '2023', 'yearType': 'operate'}
    fields = list(row)
    raw = [[row[field] for field in fields]]
    source_path = write(tmp_path / 'original.json', {'provider': 'BaoStock',
        'api': 'query_dividend_data', 'request': query, 'fields': fields,
        'error_code': '0', 'historical_available_at_verified': False,
        'raw_rows_sha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False,
            separators=(',', ':')).encode()).hexdigest(), 'raw_rows': raw})
    digest = acquisition._sha(source_path)
    source = {'symbol': '000039.SZ', 'kind': 'DIVIDEND', 'year': 2023,
        'api': 'query_dividend_data', 'request': query, 'path': str(source_path),
        'sha256': digest, 'origin': 'FROZEN_COLLECTOR_RESPONSE',
        'physical_start': 20230101, 'physical_end': 20231231,
        'request_verified': True, 'source_year_type': 'operate', 'row_count': 1}
    target = {'symbol': '000039.SZ', 'kind': 'SHARE_ACCOUNT_TERMS',
        'effective_date': 20230720, 'source_response_sha256': digest,
        'cninfo_org_id': 'gssz0000039',
        'publication_window': {'start': 20230714, 'end': 20230714},
        'fallback_publication_window': {'start': 20230706, 'end': 20230720}}
    return {'targets': write(tmp_path / 'targets.json', {'version': acquisition.TARGET_VERSION,
                 'targets': [target]}),
            'source_catalog': write(tmp_path / 'catalog.json', {'sources': [source]}),
            'output_dir': tmp_path / 'evidence'}


def change_target(package, **changes):
    value = acquisition._load(package['targets'])
    value['targets'][0].update(changes)
    write(package['targets'], value)


def metadata(*, code='000039', day='2023-07-14', title='2022年度权益分派实施公告',
             announcement_id='1212345678', has_more=False, url=None, org_id='gssz0000039'):
    instant = datetime.fromisoformat(day + 'T00:00:00+08:00')
    item = {'secCode': code, 'orgId': org_id, 'announcementTitle': title,
        'announcementId': announcement_id, 'announcementTime': int(instant.timestamp() * 1000),
        'adjunctUrl': url or f'finalpage/{day}/{announcement_id}.PDF'}
    return json.dumps({'announcements': [item], 'hasMore': has_more}, ensure_ascii=False).encode()


def empty_metadata():
    return b'{"announcements":[],"hasMore":false,"totalAnnouncement":0}'


def install_network(monkeypatch, replies):
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply, url
    monkeypatch.setattr(acquisition, '_fetch', fetch)
    monkeypatch.setattr(acquisition, '_extract_text', lambda raw: ('待审核的股份登记及资本来源原文',
        {'tool': 'test-pypdf', 'version': '1', 'page_count': 1, 'ocr_used': False}))
    return calls


def run(package, **kwargs):
    return acquisition.fetch_universe_action_announcements_v1(**package, **kwargs)


def result(package):
    return acquisition._load(next(package['output_dir'].glob('*/RESULT.json')))


def test_acquisition_retains_source_metadata_pdf_text_and_unreviewed_binding(package, monkeypatch):
    calls = install_network(monkeypatch, [metadata(), b'%PDF-1.7\noriginal'])
    summary = run(package)
    assert len(calls) == 2
    assert calls[0][0] == acquisition.QUERY_URL
    request = calls[0][1]['data'].decode()
    assert 'stock=000039' in request
    assert parse_qs(request)['seDate'] == ['2023-07-14~2023-07-14']
    own = result(package)
    document = own['documents'][0]
    assert summary['status_counts'] == {'CANDIDATES_ACQUIRED_UNREVIEWED': 1}
    assert document['status'] == 'TEXT_EXTRACTED_UNREVIEWED'
    assert document['source_response_sha256'] == own['target']['source_response_sha256']
    assert document['metadata_response_sha256'] == own['queries'][0]['response']['sha256']
    assert document['document']['sha256'] == hashlib.sha256(b'%PDF-1.7\noriginal').hexdigest()
    assert document['text']['source_sha256'] == document['document']['sha256']
    assert document['document']['text'] == document['text']
    assert document['document']['source_url'] == document['identity']['url']
    assert document['review_status'] == 'UNREVIEWED'
    assert not document['automatic_terms_resolution']
    assert not document['share_credit_date_inferred_from_listing']
    assert 'SOURCE_BOUND_REVIEWED_DECLARATION' not in json.dumps(own)
    from scripts.prepare_universe_actions_v2 import _document
    _document(document['document'], own['target']['effective_date'])


def test_resume_hash_verifies_all_artifacts_and_issues_no_network_request(package, monkeypatch):
    calls = install_network(monkeypatch, [metadata(), b'%PDF-1.7\noriginal'])
    run(package)
    before = {path: path.read_bytes() for path in package['output_dir'].rglob('*') if path.is_file()}
    run(package, resume=True)
    assert len(calls) == 2
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert (package['output_dir'] / 'ACQUISITION_RESULT_000002.json').exists()


@pytest.mark.parametrize('field', ['document', 'text'])
def test_resume_rejects_tampered_originals_before_any_network(package, monkeypatch, field):
    calls = install_network(monkeypatch, [metadata(), b'%PDF-1.7\noriginal'])
    run(package)
    path = Path(result(package)['documents'][0][field]['path'])
    path.write_bytes(path.read_bytes() + b' tampered')
    with pytest.raises(ValueError, match='RESUME_ARTIFACT_CHANGED'):
        run(package, resume=True)
    assert len(calls) == 2


def test_resume_recomputes_candidate_metadata_from_original_response(package, monkeypatch):
    calls = install_network(monkeypatch, [metadata(), b'%PDF-1.7\noriginal'])
    run(package)
    path = next(package['output_dir'].glob('*/QUERY*.receipt.json'))
    value = acquisition._load(path)
    value['candidates'][0]['metadata']['secCode'] = '000001'
    write(path, value)
    with pytest.raises(ValueError, match='RESUME_METADATA_CHANGED'):
        run(package, resume=True)
    assert len(calls) == 2


def test_resume_missing_completed_receipts_cannot_issue_duplicate_network_request(package, monkeypatch):
    calls = install_network(monkeypatch, [metadata(), b'%PDF-1.7\noriginal'])
    run(package)
    directory = next(path for path in package['output_dir'].iterdir() if path.is_dir())
    (directory / 'QUERY_W0_K0_P1.receipt.json').unlink()
    (directory / 'QUERY_W0_K0_P1.started.json').unlink()
    with pytest.raises(ValueError, match='RESUME_QUERY_RECEIPT_MISSING'):
        run(package, resume=True)
    assert len(calls) == 2


def test_source_whole_sealed_scope_is_rejected_before_source_hash_or_body(package, monkeypatch):
    value = acquisition._load(package['source_catalog'])
    value['sources'][0]['physical_end'] = 20250801
    write(package['source_catalog'], value)
    monkeypatch.setattr(acquisition, '_action_source_readonly', lambda *_: pytest.fail('original read'))
    monkeypatch.setattr(acquisition, '_sha', lambda *_: pytest.fail('hash read'))
    monkeypatch.setattr(acquisition, '_fetch', lambda *_a, **_k: pytest.fail('network'))
    with pytest.raises(FinalTestAccessViolation):
        run(package)
    assert not package['output_dir'].exists()


def test_sealed_target_window_is_rejected_before_hash_or_network(package, monkeypatch):
    change_target(package, effective_date=20250801,
                  publication_window={'start': 20250801, 'end': 20250801})
    monkeypatch.setattr(acquisition, '_sha', lambda *_: pytest.fail('hash read'))
    monkeypatch.setattr(acquisition, '_fetch', lambda *_a, **_k: pytest.fail('network'))
    with pytest.raises(FinalTestAccessViolation):
        run(package)


@pytest.mark.parametrize('changes,reason', [
    ({'effective_date': 20230721}, 'UNIQUE_SOURCE_EVENT_REQUIRED'),
    ({'symbol': '000001.SZ'}, 'UNIQUE_REGISTERED_SOURCE_REQUIRED'),
    ({'source_response_sha256': 'f' * 64}, 'UNIQUE_REGISTERED_SOURCE_REQUIRED'),
    ({'publication_window': {'start': 20230713, 'end': 20230713}}, 'MUST_BIND_PLAN_DATE'),
    ({'publication_window': {'start': 20230714, 'end': 20230721}}, 'AFTER_EFFECTIVE_DATE'),
    ({'publication_window': {'start': 20230601, 'end': 20230714}}, 'TOO_WIDE'),
    ({'cninfo_org_id': '../all'}, 'ORG_ID_INVALID'),
])
def test_invalid_target_bindings_are_rejected_before_network(package, monkeypatch, changes, reason):
    change_target(package, **changes)
    monkeypatch.setattr(acquisition, '_fetch', lambda *_a, **_k: pytest.fail('network'))
    with pytest.raises(ValueError, match=reason):
        run(package)


def test_source_original_sha_must_still_match_registered_catalog(package, monkeypatch):
    source = acquisition._load(package['source_catalog'])['sources'][0]
    path = Path(source['path'])
    path.write_text(path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
    monkeypatch.setattr(acquisition, '_fetch', lambda *_a, **_k: pytest.fail('network'))
    with pytest.raises(ValueError, match='ORIGINAL_SHA_CHANGED'):
        run(package)


def test_exact_empty_primary_allows_only_the_declared_frozen_fallback(package, monkeypatch):
    calls = install_network(monkeypatch, [empty_metadata(), metadata(day='2023-07-13'), b'%PDF-1.7'])
    run(package)
    assert len(calls) == 3
    assert parse_qs(calls[1][1]['data'].decode())['seDate'] == ['2023-07-06~2023-07-20']
    assert result(package)['documents'][0]['identity']['published_date'] == 20230713


@pytest.mark.parametrize('failure', [TimeoutError('service timeout'), b'{"unexpected":"response"}', b'not-json'])
def test_query_failures_are_recorded_without_fallback_or_found_none(package, monkeypatch, failure):
    calls = install_network(monkeypatch, [failure])
    run(package)
    own = result(package)
    assert len(calls) == 1
    assert own['status'] == 'QUERY_FAILED'
    assert own['queries'][0]['status'] == 'FAILED'
    assert not own['global_no_announcement_claim']
    assert 'FOUND_NONE' not in json.dumps(own)
    run(package, resume=True)
    assert len(calls) == 1


def test_frozen_org_id_is_bound_to_query_and_returned_metadata(package, monkeypatch):
    calls = install_network(monkeypatch, [metadata(), b'%PDF-1.7'])
    run(package)
    assert 'stock=000039%2Cgssz0000039' in calls[0][1]['data'].decode()


@pytest.mark.parametrize('item_changes,reason', [
    ({'code': '000001'}, 'SECURITY_MISMATCH'),
    ({'org_id': 'another_org_id'}, 'ORG_ID_MISMATCH'),
    ({'day': '2023-07-21'}, 'PUBLICATION_OUTSIDE_FROZEN_WINDOW'),
    ({'title': '关于股东大会通过权益分派议案的公告'}, 'TITLE_NOT_TARGET'),
])
def test_metadata_stock_date_and_title_are_required_for_download(package, monkeypatch, item_changes, reason):
    value = acquisition._load(package['targets'])
    del value['targets'][0]['fallback_publication_window']
    write(package['targets'], value)
    calls = install_network(monkeypatch, [metadata(**item_changes)])
    run(package)
    own = result(package)
    assert len(calls) == 1
    assert own['status'] == 'NO_MATCHING_CANDIDATE'
    assert reason in own['queries'][0]['rejected'][0]['reason']
    assert not own['documents']


def test_sealed_metadata_is_not_saved_or_downloaded(package, monkeypatch):
    calls = install_network(monkeypatch, [metadata(day='2025-08-01')])
    run(package)
    own = result(package)
    assert len(calls) == 1
    assert own['status'] == 'QUERY_FAILED'
    assert own['queries'][0]['error_type'] == 'FinalTestAccessViolation'
    assert 'failed_response_sha256' in own['queries'][0]
    assert not list(package['output_dir'].glob('*/QUERY*.response.json'))


def test_page_limit_is_two_and_never_claims_a_complete_search(package, monkeypatch):
    calls = install_network(monkeypatch, [metadata(title='议案', has_more=True),
                                          metadata(title='议案', has_more=True)])
    run(package)
    own = result(package)
    assert len(calls) == 2
    assert own['status'] == 'QUERY_PAGE_LIMIT_REACHED'
    assert not own['global_no_announcement_claim']


@pytest.mark.parametrize('url', [
    'http://static.cninfo.com.cn/finalpage/2023-07-14/1212345678.PDF',
    'https://static.cninfo.com.cn.evil.test/finalpage/2023-07-14/1212345678.PDF',
    'https://evil.test/finalpage/2023-07-14/1212345678.PDF',
    'https://static.cninfo.com.cn/arbitrary/1212345678.PDF',
    'https://static.cninfo.com.cn/finalpage/2023-07-15/1212345678.PDF',
    'https://static.cninfo.com.cn/finalpage/2023-07-14/1212345679.PDF',
    'https://static.cninfo.com.cn/finalpage/2023-07-14/../1212345678.PDF',
    'https://static.cninfo.com.cn/finalpage/2023-07-14/%2e%2e/1212345678.PDF',
    'https://user:password@static.cninfo.com.cn/finalpage/2023-07-14/1212345678.PDF',
    '//evil.test/announcement.pdf',
])
def test_unsafe_or_cross_window_pdf_urls_are_rejected_without_pdf_request(package, monkeypatch, url):
    calls = install_network(monkeypatch, [metadata(url=url)])
    run(package)
    assert len(calls) == 1
    assert result(package)['status'] == 'QUERY_FAILED'
    assert not list(package['output_dir'].glob('*/*.pdf'))


def test_redirect_validation_rejects_host_or_date_before_following():
    handler = acquisition._OfficialRedirects({'published_date': 20230714,
        'symbol': '000039.SZ', 'announcement_id': '1212345678'})
    request = Request('https://static.cninfo.com.cn/finalpage/2023-07-14/1212345678.PDF')
    with pytest.raises(ValueError, match='HOST_NOT_ALLOWED'):
        handler.redirect_request(request, None, 302, '', {}, 'https://evil.test/announcement.pdf')
    with pytest.raises(ValueError, match='PUBLICATION_DATE_CONFLICT'):
        handler.redirect_request(request, None, 302, '', {},
            'https://static.cninfo.com.cn/finalpage/2023-07-15/1212345678.PDF')


def test_query_redirect_cannot_convert_post_to_unrestricted_get():
    handler = acquisition._OfficialRedirects({'published_date': None})
    with pytest.raises(ValueError, match='QUERY_REDIRECT_NOT_ALLOWED'):
        handler.redirect_request(Request(acquisition.QUERY_URL, data=b'stock=000039'),
                                 None, 302, '', {}, acquisition.QUERY_URL)


@pytest.mark.parametrize('limit', [acquisition.METADATA_LIMIT, acquisition.PDF_LIMIT])
def test_network_read_is_bounded_and_uses_timeout(monkeypatch, limit):
    observed = {}
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def geturl(self):
            return acquisition.QUERY_URL
        def read(self, size):
            observed['size'] = size
            return b'x' * size
    class Opener:
        def open(self, req, timeout):
            observed['timeout'] = timeout
            return Response()
    monkeypatch.setattr(acquisition, 'build_opener', lambda *_: Opener())
    with pytest.raises(ValueError, match='TOO_LARGE'):
        acquisition._fetch(acquisition.QUERY_URL, limit=limit)
    assert observed == {'size': limit + 1, 'timeout': 30}


def test_pdf_without_text_preserves_original_with_failure_evidence(package, monkeypatch):
    calls = install_network(monkeypatch, [metadata(), b'%PDF-1.7'])
    def extraction_fails(raw):
        raise ModuleNotFoundError('pypdf')
    monkeypatch.setattr(acquisition, '_extract_text', extraction_fails)
    run(package)
    own = result(package)['documents'][0]
    assert len(calls) == 2
    assert own['status'] == 'PDF_ACQUIRED_TEXT_FAILED'
    assert Path(own['document']['path']).read_bytes() == b'%PDF-1.7'
    assert own['extraction_error_type'] == 'ModuleNotFoundError'
    assert not own['automatic_terms_resolution']


def test_non_pdf_download_remains_failed(package, monkeypatch):
    install_network(monkeypatch, [metadata(), b'<html>captcha</html>'])
    run(package)
    assert result(package)['status'] == 'PDF_ACQUISITION_INCOMPLETE'
    assert result(package)['documents'][0]['status'] == 'FAILED'
    assert result(package)['documents'][0]['failed_response_sha256'] == hashlib.sha256(b'<html>captcha</html>').hexdigest()
    assert not list(package['output_dir'].glob('*/*.pdf'))


def test_existing_output_requires_resume_and_changed_targets_are_rejected(package, monkeypatch):
    calls = install_network(monkeypatch, [metadata(), b'%PDF-1.7'])
    run(package)
    with pytest.raises(ValueError, match='NEW_OUTPUT_OR_UNCHANGED_RESUME_REQUIRED'):
        run(package)
    change_target(package, cninfo_org_id='changed_org_id')
    with pytest.raises(ValueError, match='NEW_OUTPUT_OR_UNCHANGED_RESUME_REQUIRED'):
        run(package, resume=True)
    assert len(calls) == 2


def test_output_cannot_escape_the_targets_package(package, monkeypatch):
    package['output_dir'] = package['targets'].parent.parent / 'outside'
    monkeypatch.setattr(acquisition, '_fetch', lambda *_a, **_k: pytest.fail('network'))
    with pytest.raises(ValueError, match='WITHIN_TARGET_PACKAGE'):
        run(package)


def test_interrupted_request_has_no_duplicate_retry(package, monkeypatch):
    install_network(monkeypatch, [metadata(), b'%PDF-1.7'])
    run(package)
    own = result(package)
    directory = Path(own['documents'][0]['document']['path']).parent
    (directory / 'RESULT.json').unlink()
    (directory / 'QUERY_W0_K0_P1.receipt.json').unlink()
    (package['output_dir'] / 'ACQUISITION_RESULT_000001.json').unlink()
    monkeypatch.setattr(acquisition, '_fetch', lambda *_a, **_k: pytest.fail('duplicate request'))
    run(package, resume=True)
    assert result(package)['status'] == 'QUERY_FAILED'
    assert result(package)['queries'][0]['status'] == 'INCOMPLETE_ATTEMPT'


def test_interrupted_pdf_with_bound_artifact_can_extract_without_network(package, monkeypatch):
    calls = install_network(monkeypatch, [metadata(), b'%PDF-1.7'])
    run(package)
    own = result(package)
    directory = Path(own['documents'][0]['document']['path']).parent
    (directory / 'RESULT.json').unlink()
    (directory / 'PDF_1212345678.receipt.json').unlink()
    (directory / 'PDF_1212345678.txt').unlink()
    (package['output_dir'] / 'ACQUISITION_RESULT_000001.json').unlink()
    run(package, resume=True)
    assert len(calls) == 2
    assert result(package)['documents'][0]['status'] == 'TEXT_EXTRACTED_UNREVIEWED'


@pytest.mark.parametrize('org_id', [None, '', 'missing'])
def test_missing_frozen_org_id_fails_before_original_hash_or_network(package, monkeypatch, org_id):
    value = acquisition._load(package['targets'])
    if org_id == 'missing':
        del value['targets'][0]['cninfo_org_id']
    else:
        value['targets'][0]['cninfo_org_id'] = org_id
    write(package['targets'], value)
    monkeypatch.setattr(acquisition, '_action_source_readonly', lambda *_: pytest.fail('original read'))
    monkeypatch.setattr(acquisition, '_sha', lambda *_: pytest.fail('hash read'))
    monkeypatch.setattr(acquisition, '_fetch', lambda *_a, **_k: pytest.fail('network'))
    with pytest.raises(ValueError, match='FROZEN_ORG_ID_REQUIRED'):
        run(package)
    assert not package['output_dir'].exists()


def test_query_cannot_silently_use_code_only_stock():
    with pytest.raises(ValueError, match='FROZEN_ORG_ID_REQUIRED'):
        acquisition._query({'symbol': '000039.SZ'}, {'start': 20230714, 'end': 20230714}, '实施公告', 1)


def crash_before_pdf_receipt(package, monkeypatch, pdf, *, extractor=None):
    calls = install_network(monkeypatch, [metadata(), pdf])
    if extractor is not None:
        monkeypatch.setattr(acquisition, '_extract_text', extractor)
    original_write = acquisition._write
    def interrupted_write(path, value):
        if Path(path).name == 'PDF_1212345678.receipt.json':
            assert Path(path).with_name('PDF_1212345678.txt').is_file()
            raise KeyboardInterrupt('simulated interruption after text save')
        original_write(path, value)
    monkeypatch.setattr(acquisition, '_write', interrupted_write)
    with pytest.raises(KeyboardInterrupt, match='after text save'):
        run(package)
    monkeypatch.setattr(acquisition, '_write', original_write)
    monkeypatch.setattr(acquisition, '_fetch', lambda *_a, **_k: pytest.fail('duplicate network request'))
    return calls, next(package['output_dir'].glob('*/PDF_1212345678.txt'))


def test_crash_after_real_text_save_recovers_by_deterministic_reextraction(package, monkeypatch):
    extractor = acquisition._extract_text
    pdf = real_pdf_bytes()
    calls, text_path = crash_before_pdf_receipt(package, monkeypatch, pdf, extractor=extractor)
    before = {path: path.read_bytes() for path in package['output_dir'].rglob('*') if path.is_file()}
    assert not list(package['output_dir'].glob('*/PDF_1212345678.receipt.json'))
    assert not list(package['output_dir'].glob('*/RESULT.json'))
    run(package, resume=True)
    own = result(package)['documents'][0]
    assert len(calls) == 2
    assert own['status'] == 'TEXT_EXTRACTED_UNREVIEWED'
    assert own['text']['path'] == str(text_path)
    assert own['text']['sha256'] == hashlib.sha256(before[text_path]).hexdigest()
    assert own['text']['extractor_provenance']['tool'] == 'pypdf'
    assert all(path.read_bytes() == raw for path, raw in before.items())
    run(package, resume=True)
    assert len(calls) == 2


@pytest.mark.parametrize('changed_extractor', [False, True])
def test_crash_recovery_rejects_changed_existing_text_without_failed_receipt(package, monkeypatch, changed_extractor):
    calls, text_path = crash_before_pdf_receipt(package, monkeypatch, b'%PDF-1.7')
    if changed_extractor:
        monkeypatch.setattr(acquisition, '_extract_text', lambda raw: ('different deterministic output',
            {'tool': 'test-pypdf', 'version': '2', 'page_count': 1, 'ocr_used': False}))
    else:
        text_path.write_bytes(b'tampered existing text')
    existing = text_path.read_bytes()
    with pytest.raises(ValueError, match='RESUME_EXISTING_TEXT_CHANGED'):
        run(package, resume=True)
    assert len(calls) == 2
    assert text_path.read_bytes() == existing
    assert not list(package['output_dir'].glob('*/PDF_1212345678.receipt.json'))
    assert not list(package['output_dir'].glob('*/RESULT.json'))
    assert not list(package['output_dir'].glob('ACQUISITION_RESULT_*.json'))


def test_category15_requires_its_own_reference_titles_and_adjust_source():
    assert acquisition._title_allowed('关于资本重整及除权参考价说明的公告', 'CATEGORY15_REFERENCE_PRICE')[1]
    assert not acquisition._title_allowed('2022年度权益分派实施公告', 'CATEGORY15_REFERENCE_PRICE')[1]
    assert acquisition.KINDS['CATEGORY15_REFERENCE_PRICE'] == ('资本重整', '除权参考价')


def test_cninfo_null_announcement_list_with_explicit_zero_count_is_valid():
    target = {'symbol': '000039.SZ', 'kind': 'SHARE_ACCOUNT_TERMS', 'cninfo_org_id': 'gssz0000039'}
    assert acquisition._parse_metadata(b'{"announcements":null,"totalAnnouncement":0,"hasMore":false}',
        target, {'start': 20230714, 'end': 20230714}) == ([], [], False)


@pytest.mark.parametrize('title', ['2022年度权益分派实施公告', '2022年度利润分配实施公告',
    '2022年度利润分配及资本公积金转增股本实施公告', '<em>权益分派</em>实施公告'])
def test_only_precise_implementation_title_classes_are_accepted(title):
    assert acquisition._title_allowed(title, 'SHARE_ACCOUNT_TERMS')[1]


@pytest.mark.parametrize('symbol,title', [
    ('000100.SZ', '关于2022年年度权益分派的实施公告'),
    ('002300.SZ', '2022年年度分红派息实施公告'),
    ('002824.SZ', '2022年年度权益分派实施的公告'),
    ('002885.SZ', '2022年度分红派息、转增股本的实施公告'),
    ('002977.SZ', '关于2022年年度权益分派的实施公告'),
    ('003031.SZ', '关于公司2023年年度权益分派实施的公告'),
    ('003038.SZ', '2023年度权益分配实施公告'),
    ('300421.SZ', '2022年度分红派息实施公告'),
    ('300553.SZ', '2022年度权益分派实施的公告'),
    ('300818.SZ', '2023年度分红派息实施公告'),
    ('300842.SZ', '2023年年度权益分派实施的公告'),
    ('301030.SZ', '2023年年度权益分派实施的公告'),
    ('301303.SZ', '关于2023年度权益分派实施的公告'),
    ('301395.SZ', '关于2023年度权益分派实施的公告'),
])
def test_fourteen_real_rejected_implementation_titles_are_selected_without_changing_metadata(symbol, title):
    target = {'symbol': symbol, 'kind': 'SHARE_ACCOUNT_TERMS', 'cninfo_org_id': 'frozen_official_org_id'}
    raw = metadata(code=symbol[:6], title=title, org_id=target['cninfo_org_id'])
    candidates, rejected, more = acquisition._parse_metadata(raw, target,
        {'start': 20230714, 'end': 20230714})
    assert len(candidates) == 1
    assert candidates[0]['metadata']['announcementTitle'] == title
    assert candidates[0]['metadata']['secCode'] == symbol[:6]
    assert not rejected
    assert not more
    assert acquisition.KINDS['SHARE_ACCOUNT_TERMS'] == ('实施公告',)


@pytest.mark.parametrize('action', ['权益分派', '权益分配', '利润分配', '分红派息', '转增股本'])
@pytest.mark.parametrize('title_format', ['{}实施公告', '{}的实施公告', '{}实施的公告', '{}的实施的公告'])
def test_equivalent_implementation_phrasing_allows_only_optional_single_de(action, title_format):
    assert acquisition._title_allowed('2023年度' + title_format.format(action), 'SHARE_ACCOUNT_TERMS')[1]


@pytest.mark.parametrize('kind', ['SHARE_ACCOUNT_TERMS', 'CASH_ACTION_DATE'])
@pytest.mark.parametrize('title', [
    '2022年度权益分派预案公告',
    '2022年度权益分派实施提示公告',
    '2022年度权益分派实施公告的提示性公告',
    '关于更正2022年度权益分派实施公告',
    '更正后的2022年度利润分配实施的公告',
    '关于补充2022年度权益分派的实施公告',
    '关于修订2022年度转增股本实施公告',
    '关于修改2022年度权益分配实施的公告',
    '关于取消2022年度权益分派实施公告',
    '关于撤销2022年度利润分配实施公告',
    '关于延期2022年度转增股本实施公告',
    '关于终止2022年度分红派息实施公告',
    '2022年度权益分派的的实施公告',
    '2022年度权益分派实施的的公告',
    '2022年度股东权益变化实施公告',
    '关于2022年度权益分派实施情况的公告',
    '2022年度权益分派实施公告摘要',
    '2022年度权益分派实施公告（更正）',
])
def test_equivalent_implementation_title_rule_rejects_non_body_or_unanchored_titles(kind, title):
    assert not acquisition._title_allowed(title, kind)[1]


def test_publication_timestamp_is_converted_to_shanghai_date():
    stamp = int(datetime(2023, 7, 13, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert acquisition._published(stamp) == (20230714, '2023-07-14T00:00:00+08:00')


def make_adjust_target(package, kind):
    row = {'code': 'sz.000039', 'dividOperateDate': '2023-07-20',
           'foreAdjustFactor': '0.9', 'backAdjustFactor': '1.1', 'adjustFactor': '1.1'}
    fields = list(row)
    raw = [[row[field] for field in fields]]
    query = {'code': 'sz.000039', 'start_date': '2023-07-01', 'end_date': '2023-07-31'}
    path = write(package['targets'].parent / 'adjust_original.json', {'provider': 'BaoStock',
        'api': 'query_adjust_factor', 'request': query, 'fields': fields,
        'error_code': '0', 'historical_available_at_verified': False,
        'raw_rows_sha256': hashlib.sha256(json.dumps(raw, separators=(',', ':')).encode()).hexdigest(),
        'raw_rows': raw})
    digest = acquisition._sha(path)
    write(package['source_catalog'], {'sources': [{'symbol': '000039.SZ', 'kind': 'ADJUST',
        'api': 'query_adjust_factor', 'request': query, 'path': str(path), 'sha256': digest,
        'origin': 'FROZEN_COLLECTOR_RESPONSE', 'physical_start': 20230701, 'physical_end': 20230731,
        'request_verified': True, 'source_year_type': None, 'row_count': 1}]})
    change_target(package, kind=kind, source_response_sha256=digest)


@pytest.mark.parametrize('kind,title,keyword_count', [
    ('CATEGORY15_REFERENCE_PRICE', '关于资本重整及除权参考价说明的公告', 2),
    ('CORPORATE_ACTION_DATE', '关于股本变动的公告', 3),
])
def test_adjust_bound_date_and_category15_targets_keep_exact_metadata(package, monkeypatch, kind, title, keyword_count):
    make_adjust_target(package, kind)
    calls = install_network(monkeypatch, [metadata(title=title)] * keyword_count + [b'%PDF-1.7'])
    run(package)
    own = result(package)
    assert len(calls) == keyword_count + 1
    assert own['target']['source_binding']['api'] == 'query_adjust_factor'
    assert len(own['documents']) == 1
    assert own['documents'][0]['metadata']['announcementTitle'] == title
    for _, query in calls[:-1]:
        assert parse_qs(query['data'].decode())['stock'] == ['000039,gssz0000039']
    assert not own['automatic_terms_resolution']


def test_cash_action_date_uses_dividend_source_and_explicit_cash_keywords(package, monkeypatch):
    change_target(package, kind='CASH_ACTION_DATE')
    calls = install_network(monkeypatch, [metadata(title='2022年度利润分配实施公告')] * 2 + [b'%PDF-1.7'])
    run(package)
    assert len(calls) == 3
    assert result(package)['target']['source_binding']['api'] == 'query_dividend_data'
    assert [parse_qs(call[1]['data'].decode())['searchkey'][0] for call in calls[:-1]] == ['权益分派', '利润分配']


@pytest.mark.parametrize('title,allowed', [
    ('关于股本变化的公告', True),
    ('关于资本公积金转增股本实施结果的公告', True),
    ('关于重整计划执行完毕的公告', True),
    ('2022年度权益分派实施公告', True),
    ('关于修订公司章程的公告', False),
    ('关于权益分派议案的董事会公告', False),
    ('关于股本管理制度的公告', False),
])
def test_corporate_action_date_titles_require_a_concrete_event_class(title, allowed):
    assert acquisition._title_allowed(title, 'CORPORATE_ACTION_DATE')[1] is allowed


def test_resumed_sealed_pdf_date_is_guarded_before_hash_or_network(package, monkeypatch):
    install_network(monkeypatch, [metadata(), b'%PDF-1.7'])
    run(package)
    path = next(package['output_dir'].glob('*/PDF*.receipt.json'))
    own = acquisition._load(path)
    own['identity']['published_date'] = 20250801
    write(path, own)
    original_sha = acquisition._sha
    def guarded_sha(path):
        if str(path).endswith('.pdf'):
            pytest.fail('sealed PDF hash')
        return original_sha(path)
    monkeypatch.setattr(acquisition, '_sha', guarded_sha)
    monkeypatch.setattr(acquisition, '_fetch', lambda *_a, **_k: pytest.fail('network'))
    with pytest.raises(FinalTestAccessViolation):
        run(package, resume=True)


def real_pdf_bytes():
    pypdf = pytest.importorskip('pypdf')
    generic = pypdf.generic
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    font = generic.DictionaryObject({generic.NameObject('/Type'): generic.NameObject('/Font'),
        generic.NameObject('/Subtype'): generic.NameObject('/Type1'),
        generic.NameObject('/BaseFont'): generic.NameObject('/Helvetica')})
    page[generic.NameObject('/Resources')] = generic.DictionaryObject({generic.NameObject('/Font'):
        generic.DictionaryObject({generic.NameObject('/F1'): writer._add_object(font)})})
    content = generic.DecodedStreamObject()
    content.set_data(b'BT /F1 12 Tf 10 50 Td (Bound original text) Tj ET')
    page[generic.NameObject('/Contents')] = writer._add_object(content)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_real_lazy_pypdf_extraction_preserves_provenance():
    pypdf = pytest.importorskip('pypdf')
    text, provenance = acquisition._extract_text(real_pdf_bytes())
    assert 'Bound original text' in text
    assert provenance['tool'] == 'pypdf'
    assert provenance['version'] == pypdf.__version__
    assert provenance['page_count'] == 1
    assert not provenance['ocr_used']
