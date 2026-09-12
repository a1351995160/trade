"""合成恢复测试，不连接提供者。"""
import importlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def driver(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    module = importlib.import_module('run_baostock_account_v1')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'active', lambda: ({}, datetime.now(timezone.utc)+timedelta(hours=1)))
    return module


def test_cache_retry_and_unapproved_interruption(driver, monkeypatch):
    import chanlun_trader.synthetic_batch_resources as resources
    import chanlun_trader.data.minute.baostock_provider as provider
    monkeypatch.setattr(resources, 'worker_resource_handshake', lambda: None)
    monkeypatch.setattr(driver, 'resume_revision', lambda *args: {})
    monkeypatch.setattr(driver, 'validate_batch', lambda batch: None)
    driver.save(driver.ROOT/'ACQUISITION_RESUME_V1.json', {})
    driver.save(driver.ROOT/'READ_PLAN.json', dict(symbols=['002853.SZ'], start='2022-07-22', end='2024-07-31', purpose='TEST'))
    calls = []

    class Response:
        error_code = '0'
        error_msg = ''
        fields = []

        def next(self):
            return False

    class Provider:
        @contextmanager
        def session(self):
            yield self

        @property
        def bs(self):
            return self

        def query_history_k_data_plus(self, **query):
            calls.append(query)
            return Response()

    monkeypatch.setattr(provider, 'BaoStock5MinProvider', Provider)
    original = driver.ROOT/'responses/002853.SZ/1.started.json'
    driver.save(original, {'interrupted':True})
    original_bytes = original.read_bytes()
    driver.fetch(0, 0)
    assert len(calls) == 2
    assert (original.parent/'attempt-2/1.access.json').exists()
    assert not (original.parent/'1.json').exists()
    driver.fetch(0, 0)
    assert len(calls) == 2
    assert original.read_bytes() == original_bytes
    retry = original.parent/'attempt-2/1.json'
    retry.unlink()
    with pytest.raises(PermissionError, match='NO_AUTOMATIC_RETRY'):
        driver.fetch(0, 0)
    assert len(calls) == 2
    assert driver.response_directory('OTHER', '1') == driver.ROOT/'responses/OTHER'


@pytest.mark.parametrize('failure', ['limit', 'expiry', 'revoked', 'unsettled'])
def test_resume_stops_before_worker(driver, monkeypatch, failure):
    import chanlun_trader.synthetic_batch_resources as resources
    monkeypatch.setattr(driver, 'resume_revision', lambda: {'total_seconds':21600})
    driver.save(driver.ROOT/'READ_PLAN.json', {'symbols':['S']*1201})
    for batch in range(6):
        driver.save(driver.ROOT/'quality'/f'batch-{batch}.json', {'passed':True})
    if failure == 'limit':
        driver.save(driver.ROOT/'resources/old.json', {'elapsed_seconds':21600})
    if failure == 'expiry':
        monkeypatch.setattr(driver, 'active', lambda: ({}, datetime.now(timezone.utc)-timedelta(seconds=1)))
    if failure == 'revoked':
        def revoked():
            raise PermissionError('APPROVAL_REVOKED')
        monkeypatch.setattr(driver, 'active', revoked)
    if failure == 'unsettled':
        driver.save(driver.ROOT/'resources/resume-fetch-1200.started.json', {'pid':1})
    def forbidden(*args, **kwargs):
        pytest.fail('不得启动worker')
    monkeypatch.setattr(resources, 'run_bounded_worker', forbidden)
    with pytest.raises(PermissionError):
        driver.resume_acquire()
