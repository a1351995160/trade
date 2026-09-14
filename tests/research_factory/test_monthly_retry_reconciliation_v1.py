from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from reconcile_monthly_timeout_v1 import verify


def values():
    original = {'method': 'query_adjust_factor', 'query': {
        'code': 'sz.002803', 'start_date': '2025-07-03', 'end_date': '2026-07-31'}}
    response = {**original, 'error_code': '0', 'rows': [
        {'code': 'sz.002803', 'dividOperateDate': '2026-06-24'}]}
    return original, response, {'sha256': 'digest', 'row_count': 1}


def test_exact_response_reusable():
    verify(*values(), 'digest')


@pytest.mark.parametrize('change', ['hash', 'count', 'query', 'error', 'code', 'date'])
def test_conflicting_response_rejected(change):
    original, response, access = values()
    if change == 'hash': access['sha256'] = 'other'
    if change == 'count': access['row_count'] = 2
    if change == 'query': response['query'] = {}
    if change == 'error': response['error_code'] = 'failed'
    if change == 'code': response['rows'][0]['code'] = 'other'
    if change == 'date': response['rows'][0]['dividOperateDate'] = '2026-08-01'
    with pytest.raises(PermissionError):
        verify(original, response, access, 'digest')
