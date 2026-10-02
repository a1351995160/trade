"""源哈希和离线归档哈希之前也必须执行封存物理范围检查。"""
import json
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.research.guard import FinalTestAccessViolation
from chanlun_trader.research_factory.research_evidence_v1 import verify_job_evidence
from scripts.run_strategy_account_v1 import freeze_config, validate_sources
from test_universe_submission_v1 import public_universe_case


@pytest.mark.parametrize('field', ['share_credit_date', 'tradable_date'])
def test_share_future_dates_are_rejected_before_quote_read(tmp_path, monkeypatch, field):
    from chanlun_trader.research_factory.universe_submission_v1 import _validate_frozen_item_scope
    service, request, _, _ = public_universe_case(tmp_path)
    task = service.freeze(request, service.preview(request)['preview_identity'])
    job = json.loads(Path(task['job_path']).read_text(encoding='utf-8'))
    name = next(iter(job['plans']))
    item = job['items'][name]
    path = Path(item['loader_kwargs']['path'])
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    snapshot['bundle']['events'].append({field: 20250801})
    raw = json.dumps(snapshot).encode()
    path.write_bytes(raw)
    item['loader_kwargs']['sha256'] = hashlib.sha256(raw).hexdigest()
    def forbidden(*args, **kwargs):
        pytest.fail('封存股份日期被拒绝前不应读取行情')
    monkeypatch.setattr(pd, 'read_parquet', forbidden)
    with pytest.raises(FinalTestAccessViolation):
        _validate_frozen_item_scope(item, job['input_identity'], job['plans'][name]['backend']['window'])


@pytest.mark.parametrize('archive', [False, True])
def test_future_parquet_is_rejected_before_any_quote_hash(tmp_path, monkeypatch, archive):
    service, request, _, _ = public_universe_case(tmp_path)
    task = service.freeze(request, service.preview(request)['preview_identity'])
    job = json.loads(Path(task['job_path']).read_text(encoding='utf-8'))
    item = next(iter(job['items'].values()))
    snapshot = json.loads(Path(item['loader_kwargs']['path']).read_text(encoding='utf-8'))
    info = snapshot['frames']['daily']
    path = Path(info['path'])
    if archive:
        path = Path(job['root']) / 'source-archive' / (info['sha256'] + '_' + path.name)
    frame = pd.read_parquet(path)
    frame.loc[frame.index[-1], 'date'] = 20250801
    frame.to_parquet(path, index=False)
    original = Path.read_bytes
    def checked_bytes(self):
        if self == path:
            pytest.fail('未先阻断封存行情就开始读取/哈希内容')
        return original(self)
    monkeypatch.setattr(Path, 'read_bytes', checked_bytes)
    if archive:
        result = verify_job_evidence(task['job_path'], name=next(iter(job['plans'])))
        assert result['status'] == 'FAIL' and result['advance_allowed'] is False
        assert any('2025' in value or 'holdout' in value.lower() or '封存' in value for value in result['reasons'])
    else:
        with pytest.raises((PermissionError, ValueError, FinalTestAccessViolation)):
            validate_sources(job)


def test_frame_date_axis_cannot_be_changed_to_bypass_scope_guard(tmp_path):
    service, request, _, _ = public_universe_case(tmp_path)
    task = service.freeze(request, service.preview(request)['preview_identity'])
    job = json.loads(Path(task['job_path']).read_text(encoding='utf-8'))
    item = next(iter(job['items'].values()))
    path = Path(item['loader_kwargs']['path'])
    value = json.loads(path.read_text(encoding='utf-8'))
    value['frames']['daily']['date_columns'] = ['open']
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='UNIVERSE_FROZEN_SNAPSHOT_CHANGED'):
        validate_sources(job)


def test_first_freeze_rejects_future_footer_before_dependency_hash_or_archive(tmp_path, monkeypatch):
    service, request, _, _ = public_universe_case(tmp_path)
    task = service.freeze(request, service.preview(request)['preview_identity'])
    config_path = Path(task['job_path']).parent.parent / 'CONFIG.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    snapshot = json.loads(Path(config['items'][0]['loader_kwargs']['path']).read_text(encoding='utf-8'))
    path = Path(snapshot['frames']['daily']['path'])
    frame = pd.read_parquet(path)
    frame.loc[frame.index[-1], 'date'] = 20250801
    frame.to_parquet(path, index=False)
    original = Path.read_bytes
    def checked_bytes(self):
        if self == path:
            pytest.fail('首次冻结不能先读取/哈希封存行情再检查范围')
        return original(self)
    monkeypatch.setattr(Path, 'read_bytes', checked_bytes)
    root = tmp_path / 'direct_freeze'
    with pytest.raises((PermissionError, ValueError, FinalTestAccessViolation)):
        freeze_config(config, root)
    assert not root.exists()
